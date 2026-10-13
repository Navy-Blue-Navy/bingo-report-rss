import hashlib
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET


SOURCE_URL = "https://keizai-report.com/web/"
OUTPUT_FILE = "bingo_report.xml"

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    )
}


def fetch_page():
    for attempt in range(1, 4):
        try:
            print(f"取得 {attempt}/3")

            response = requests.get(
                SOURCE_URL,
                headers=HEADERS,
                timeout=30,
            )

            print(f"HTTP: {response.status_code}")
            response.raise_for_status()

            return response.text

        except requests.RequestException as e:
            print(f"取得失敗: {e}")

            if attempt < 3:
                print("10秒待って再試行します。")
                time.sleep(10)

    return None


def clean_text(text):
    return re.sub(r"\s+", " ", text).strip()


def make_guid(number):
    return hashlib.sha256(
        f"bingo-report|{number}".encode("utf-8")
    ).hexdigest()


def load_existing_items():
    items = {}

    if not os.path.exists(OUTPUT_FILE):
        return items

    try:
        tree = ET.parse(OUTPUT_FILE)
        root = tree.getroot()

        for item in root.findall("./channel/item"):
            guid = item.findtext("guid", "").strip()

            if not guid:
                continue

            items[guid] = {
                "title": item.findtext("title", ""),
                "link": item.findtext("link", ""),
                "description": item.findtext(
                    "description", ""
                ),
                "pubDate": item.findtext("pubDate", ""),
                "guid": guid,
            }

    except Exception as e:
        print(f"既存RSS読込失敗: {e}")

    return items


def parse_issue_caption(caption):
    """
    例:
    2026.10.10号 No.2324
    2026.9.1号 No.2320
    2026.8.01号 No.2317
    """

    text = clean_text(caption)

    match = re.fullmatch(
        r"(\d{4})\.(\d{1,2})\.(\d{1,2})号\s+No\.\s*(\d+)",
        text,
        re.IGNORECASE,
    )

    if not match:
        return None

    year = int(match.group(1))
    month = int(match.group(2))
    day = int(match.group(3))
    number = int(match.group(4))

    try:
        issue_date = datetime(
            year,
            month,
            day,
            12,
            0,
            0,
            tzinfo=JST,
        )
    except ValueError:
        return None

    return {
        "year": year,
        "month": month,
        "day": day,
        "number": number,
        "issue_date": issue_date,
        "caption": text,
    }


def parse_issues(html):
    soup = BeautifulSoup(html, "html.parser")

    issues = []
    seen_numbers = set()

    for block in soup.select("div.wp-block-image"):
        figure = block.find("figure")

        if figure is None:
            continue

        caption = figure.find("figcaption")
        link_tag = figure.find("a", href=True)

        if caption is None or link_tag is None:
            continue

        parsed = parse_issue_caption(
            caption.get_text(" ", strip=True)
        )

        if parsed is None:
            continue

        number = parsed["number"]

        if number in seen_numbers:
            continue

        link = urljoin(
            SOURCE_URL,
            link_tag.get("href", "").strip()
        )

        if not link.startswith(
            "https://keizai-report.com/web/"
        ):
            continue

        image_url = ""

        image = figure.find("img")

        if image is not None:
            image_url = image.get("src", "").strip()

            if image_url:
                image_url = urljoin(
                    SOURCE_URL,
                    image_url
                )

        seen_numbers.add(number)

        issues.append(
            {
                "number": number,
                "date": parsed["issue_date"],
                "caption": parsed["caption"],
                "link": link,
                "image": image_url,
            }
        )

    issues.sort(
        key=lambda x: x["number"],
        reverse=True,
    )

    return issues


def build_rss(existing_items, current_issues):
    now = datetime.now(JST)

    merged = dict(existing_items)
    new_count = 0

    for issue in current_issues:
        guid = make_guid(issue["number"])

        if guid in merged:
            continue

        title = (
            f"びんご経済レポート "
            f"{issue['caption']}"
        )

        description_parts = [
            issue["caption"]
        ]

        if issue["image"]:
            description_parts.append(
                f'<img src="{issue["image"]}" alt="{title}">'
            )

        description = "<br>".join(
            description_parts
        )

        merged[guid] = {
            "title": title,
            "link": issue["link"],
            "description": description,
            "pubDate": format_datetime(
                issue["date"]
            ),
            "guid": guid,
        }

        new_count += 1

    def get_pubdate(item):
        try:
            return parsedate_to_datetime(
                item["pubDate"]
            )
        except Exception:
            return datetime(
                1970,
                1,
                1,
                tzinfo=timezone.utc,
            )

    sorted_items = sorted(
        merged.values(),
        key=get_pubdate,
        reverse=True,
    )

    rss = ET.Element(
        "rss",
        version="2.0"
    )

    channel = ET.SubElement(
        rss,
        "channel"
    )

    ET.SubElement(
        channel,
        "title"
    ).text = "びんご経済レポート"

    ET.SubElement(
        channel,
        "link"
    ).text = SOURCE_URL

    ET.SubElement(
        channel,
        "description"
    ).text = (
        "びんご経済レポート公式サイトに"
        "掲載された各号の更新情報"
    )

    ET.SubElement(
        channel,
        "language"
    ).text = "ja"

    ET.SubElement(
        channel,
        "lastBuildDate"
    ).text = format_datetime(now)

    for data in sorted_items:
        item = ET.SubElement(
            channel,
            "item"
        )

        ET.SubElement(
            item,
            "title"
        ).text = data["title"]

        ET.SubElement(
            item,
            "link"
        ).text = data["link"]

        ET.SubElement(
            item,
            "description"
        ).text = data["description"]

        ET.SubElement(
            item,
            "pubDate"
        ).text = data["pubDate"]

        guid_el = ET.SubElement(
            item,
            "guid"
        )

        guid_el.set(
            "isPermaLink",
            "false"
        )

        guid_el.text = data["guid"]

    tree = ET.ElementTree(rss)

    ET.indent(
        tree,
        space="  "
    )

    tree.write(
        OUTPUT_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )

    return len(sorted_items), new_count


def main():
    existing_items = load_existing_items()

    print(
        f"既存RSS件数: {len(existing_items)}"
    )

    html = fetch_page()

    if html is None:
        if existing_items:
            print(
                "サイトを取得できないため、"
                "既存XMLを維持します。"
            )
            return

        raise RuntimeError(
            "びんご経済レポートを取得できず、"
            "既存XMLもありません。"
        )

    issues = parse_issues(html)

    print(
        f"現在ページの号数: {len(issues)}"
    )

    if len(issues) < 10:
        if existing_items:
            print(
                "取得件数が異常に少ないため、"
                "既存XMLを維持します。"
            )
            return

        raise RuntimeError(
            f"取得件数が少なすぎます: {len(issues)}"
        )

    print()
    print("取得した各号:")

    for i, issue in enumerate(
        issues,
        1
    ):
        print(
            f"{i:2d}. {issue['caption']}"
        )

        print(
            f"    {issue['link']}"
        )

        if issue["image"]:
            print(
                f"    画像: {issue['image']}"
            )

    total_count, new_count = build_rss(
        existing_items,
        issues,
    )

    print()
    print(
        f"今回の新規号: {new_count}"
    )

    print(
        f"RSS保存件数: {total_count}"
    )

    print(
        "保存先: "
        f"{os.path.abspath(OUTPUT_FILE)}"
    )


if __name__ == "__main__":
    main()