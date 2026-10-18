#!/usr/bin/env python3
"""
TUSUR Timetable Parser for Group 126-2 (IRET)
Fetches schedule from timetable.tusur.ru and generates iCalendar (.ics) and JSON.
"""

import argparse
import datetime
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple
import urllib.parse

import requests
from bs4 import BeautifulSoup

DEFAULT_URL = "https://timetable.tusur.ru/faculties/iret/groups/126-2"
TIMEZONE = "Asia/Tomsk"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

SUBJECT_ABBREVIATIONS = {
    "основы российской государственности": "ОРГ",
    "проектирование индивидуальной траектории": "ПИТР",
    "проектирование индивидуальной траектории развития": "ПИТР",
    "питр": "ПИТР",
    "физическая культура и спорт": "Физ-ра",
    "физическая культура": "Физ-ра",
    "физкультура": "Физ-ра",
    "фкис": "Физ-ра",
    "иностранный язык": "Английский",
    "английский": "Английский",
    "дискретная математика": "Дискретка",
    "инженерная графика": "Инж. графика",
    "информационные технологии": "Инф. технологии",
    "информатика": "Информатика",
    "история россии": "История",
    "основы искусственного интеллекта": "ОИИ",
    "оии": "ОИИ",
}

TYPE_MAPPINGS = [
    ("лекц", "Л", "🟢", "10"),
    ("лек", "Л", "🟢", "10"),
    ("практ", "П", "🔵", "9"),
    ("прак", "П", "🔵", "9"),
    ("лабор", "ЛР", "🟣", "3"),
    ("лаб", "ЛР", "🟣", "3"),
    ("курсовое проект", "КП", "🌸", "4"),
    ("кп", "КП", "🌸", "4"),
    ("курсов", "КР", "🌸", "4"),
    ("кр", "КР", "🌸", "4"),
    ("диф", "ЗсО", "🟠", "5"),
    ("с оценк", "ЗсО", "🟠", "5"),
    ("зсо", "ЗсО", "🟠", "5"),
    ("зачет", "ЗАЧ", "🟠", "5"),
    ("зачёт", "ЗАЧ", "🟠", "5"),
    ("зач", "ЗАЧ", "🟠", "5"),
    ("экзам", "ЭКЗ", "🔴", "11"),
    ("экз", "ЭКЗ", "🔴", "11"),
    ("самост", "СР", "⚪", "8"),
    ("срс", "СР", "⚪", "8"),
    ("ср", "СР", "⚪", "8"),
]


def get_type_and_emoji(kind_text: str) -> Tuple[str, str, str]:
    k = kind_text.strip().lower()
    for pattern, short_type, emoji, color_id in TYPE_MAPPINGS:
        if pattern in k:
            return short_type, emoji, color_id
    return "ЗАН", "🔵", "9"


def shorten_subject(name: str) -> str:
    clean = name.strip()
    n_lower = clean.lower()
    for full_pattern, short_name in SUBJECT_ABBREVIATIONS.items():
        if full_pattern in n_lower:
            return short_name
    return clean


def format_location(raw_place: str, comment: str, subject: str) -> str:
    s_lower = subject.lower()
    if any(p in s_lower for p in ["физическая культура", "физкульт", "фкис"]):
        return "Спорткомплекс"

    combined = f"{raw_place} {comment}".strip()
    combined_lower = combined.lower()
    if not combined:
        return ""

    building = ""
    if any(k in combined_lower for k in ["главный корпус", "ленина, 40"]) or re.search(r"\bгк\b", combined_lower):
        building = "ГК"
    elif any(k in combined_lower for k in ["радиотехнический корпус", "вершинина, 47"]) or re.search(r"\bрк\b", combined_lower):
        building = "РК"
    elif any(k in combined_lower for k in ["корпус фэт", "вершинина, 74", "фэт"]) or re.search(r"\bф\b", combined_lower):
        building = "ФЭТ"

    room_matches = re.findall(r"\b(?:[0-9]{3}[а-яёa-z]?|[0-9]{1,2}[а-яёa-z]?)\b", combined, re.IGNORECASE)
    rooms = []
    for rm in room_matches:
        r = rm.upper()
        if r not in rooms and r not in ["40", "47", "74", "126"]:
            rooms.append(r)

    if building and rooms:
        return f"{building} {', '.join(rooms)}"
    if building:
        return building
    if rooms:
        return ", ".join(rooms)

    return re.sub(r"\s+", " ", combined).strip().upper()


def discover_weeks(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    weeks = []
    for li in soup.find_all("li", class_=lambda c: c and "tile" in c):
        a = li.find("a")
        if a and a.get("href"):
            href = a["href"]
            if href.startswith("/"):
                href = urllib.parse.urljoin("https://timetable.tusur.ru", href)
            if href not in weeks:
                weeks.append(href)
    return weeks


def parse_week_html(html: str) -> List[dict]:
    soup = BeautifulSoup(html, "html.parser")
    lessons = []

    # Map cell notes and auditoriums by lesson-id
    cell_notes = {}
    cell_auds = {}
    for cell in soup.find_all("div", class_=lambda c: c and "lesson-cell" in c):
        disc = cell.find(attrs={"data-lesson-id": True})
        if disc:
            lid = disc["data-lesson-id"]
            note_span = cell.find("span", class_=lambda c: c and "note" in c)
            if note_span and note_span.get("title") and lid not in cell_notes:
                cell_notes[lid] = note_span["title"].strip()

            aud_span = cell.find("span", class_=lambda c: c and "auditoriums" in c)
            if aud_span and lid not in cell_auds:
                cell_auds[lid] = aud_span.get_text(separator=" ", strip=True)

    # Parse modals
    for modal in soup.find_all("div", class_=lambda c: c and "js-lesson-info-modal" in c):
        lid = modal.get("data-lesson-id")
        if not lid:
            continue

        title_el = modal.find("h4", class_=lambda c: c and "modal-title" in c)
        subject = title_el.get_text(strip=True) if title_el else "Занятие"

        props = {}
        body = modal.find("div", class_="modal-body")
        if body:
            for p in body.find_all("p"):
                strong = p.find("strong")
                if strong:
                    key = strong.get_text(strip=True).rstrip(":")
                    full_p_text = p.get_text(separator=" ", strip=True)
                    val = full_p_text.replace(strong.get_text(strip=True), "", 1).strip()
                    props[key] = val

        kind = props.get("Вид занятия", "")
        date_str = props.get("Дата проведения", "")
        time_str = props.get("Время проведения", "")
        teacher = props.get("Преподаватель", "")
        place = props.get("Место проведения", "")
        groups = props.get("Совместно с группами") or props.get("Группы") or "гр. 126-2"
        comment = props.get("Комментарий", "")

        if not date_str:
            continue

        try:
            d_obj = datetime.datetime.strptime(date_str, "%d.%m.%Y").date()
        except ValueError:
            continue

        start_time_str = "085000"
        end_time_str = "102500"
        if "-" in time_str:
            t_parts = time_str.split("-")
            if len(t_parts) == 2:
                s_t = t_parts[0].strip().replace(":", "")
                e_t = t_parts[1].strip().replace(":", "")
                if len(s_t) == 4:
                    start_time_str = s_t + "00"
                if len(e_t) == 4:
                    end_time_str = e_t + "00"

        raw_loc = place or cell_auds.get(lid, "")
        note_loc = comment or cell_notes.get(lid, "")
        clean_loc = format_location(raw_loc, note_loc, subject)

        type_short, emoji, color_id = get_type_and_emoji(kind)
        short_sub = shorten_subject(subject)
        summary = f"[{emoji} {type_short}] {short_sub}"

        desc_lines = [
            f"1. Предмет: {subject}",
            f"2. Вид: {kind}",
            f"3. Преподаватель: {teacher if teacher else 'Не указан'}",
            f"4. Аудитория: {clean_loc if clean_loc else 'Не указана'}",
            f"5. Группы: {groups}",
        ]
        if comment and comment not in clean_loc:
            desc_lines.append(f"Примечание: {comment}")

        description = "\n".join(desc_lines)
        uid = f"tusur-{lid}-{d_obj.strftime('%Y%m%d')}@timetable.tusur.ru"

        lessons.append({
            "id": lid,
            "subject": subject,
            "short_subject": short_sub,
            "kind": kind,
            "type_short": type_short,
            "emoji": emoji,
            "color_id": color_id,
            "date": d_obj.strftime("%Y-%m-%d"),
            "date_str": d_obj.strftime("%Y%m%d"),
            "time_start": start_time_str,
            "time_end": end_time_str,
            "time": f"{start_time_str[:2]}:{start_time_str[2:4]} - {end_time_str[:2]}:{end_time_str[2:4]}",
            "teacher": teacher,
            "location": clean_loc,
            "groups": groups,
            "note": comment,
            "uid": uid,
            "summary": summary,
            "description": description,
        })

    return lessons


def escape_ics(text: str) -> str:
    if not text:
        return ""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def fold_line(line: str) -> str:
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line

    out = []
    current_bytes = 0
    first = True
    for ch in line:
        ch_bytes = len(ch.encode("utf-8"))
        limit = 75 if first else 74
        if current_bytes + ch_bytes > limit:
            out.append("\r\n ")
            current_bytes = 1
            first = False
        out.append(ch)
        current_bytes += ch_bytes
    return "".join(out)


def generate_ics(lessons: List[dict], group_name: str = "126-2") -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//TUSUR Timetable Parser//timetable.tusur.ru//RU",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALDESC:Расписание учебных занятий группы {group_name} ИРЭТ ТУСУР",
        f"X-WR-CALNAME:Расписание ТУСУР {group_name}",
        "X-WR-TIMEZONE:Asia/Tomsk",
        "BEGIN:VTIMEZONE",
        "TZID:Asia/Tomsk",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZNAME:+07",
        "TZOFFSETFROM:+0700",
        "TZOFFSETTO:+0700",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]

    now_stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    for l in lessons:
        lines.append("BEGIN:VEVENT")
        lines.append(fold_line(f"SUMMARY:{escape_ics(l['summary'])}"))
        lines.append(f"DTSTART;TZID=Asia/Tomsk:{l['date_str']}T{l['time_start']}")
        lines.append(f"DTEND;TZID=Asia/Tomsk:{l['date_str']}T{l['time_end']}")
        lines.append(f"DTSTAMP:{now_stamp}")
        lines.append(f"UID:{l['uid']}")
        lines.append(fold_line(f"DESCRIPTION:{escape_ics(l['description'])}"))
        if l["location"]:
            lines.append(fold_line(f"LOCATION:{escape_ics(l['location'])}"))
        lines.append(f"X-COLOR-ID:{l['color_id']}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main():
    parser = argparse.ArgumentParser(description="TUSUR schedule parser to iCalendar")
    parser.add_argument("--url", default=DEFAULT_URL, help="Base group timetable URL")
    parser.add_argument("--all-weeks", action="store_true", default=True, help="Parse all weeks of semester")
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update(HEADERS)

    print(f"Fetching main page: {args.url}")
    res = session.get(args.url, timeout=15, verify=False)
    res.raise_encoding = "utf-8"
    main_html = res.text

    week_urls = discover_weeks(main_html) if args.all_weeks else [args.url]
    if not week_urls:
        week_urls = [args.url]

    print(f"Discovered {len(week_urls)} week URLs.")

    all_lessons_map = {}
    for i, w_url in enumerate(week_urls, 1):
        print(f"[{i}/{len(week_urls)}] Fetching {w_url} ...")
        try:
            w_html = main_html if w_url == args.url else session.get(w_url, timeout=15, verify=False).text
            lessons = parse_week_html(w_html)
            new_cnt = 0
            for l in lessons:
                if l["uid"] not in all_lessons_map:
                    all_lessons_map[l["uid"]] = l
                    new_cnt += 1
            print(f"  -> {len(lessons)} parsed (+{new_cnt} new).")
        except Exception as e:
            print(f"  -> Error: {e}")

    lessons_list = list(all_lessons_map.values())
    print(f"\nTotal unique events collected: {len(lessons_list)}")

    # Sort lessons by date and start time
    lessons_list.sort(key=lambda x: (x["date_str"], x["time_start"]))

    ics_content = generate_ics(lessons_list, group_name="126-2")

    with open("schedule.ics", "w", encoding="utf-8") as f:
        f.write(ics_content)
    with open("schedule-126-2.ics", "w", encoding="utf-8") as f:
        f.write(ics_content)

    with open("schedule.json", "w", encoding="utf-8") as f:
        json.dump(lessons_list, f, ensure_ascii=False, indent=2)

    print("Successfully generated schedule.ics, schedule-126-2.ics, and schedule.json!")


if __name__ == "__main__":
    main()
