"""
네이버 예약 자동화 스크립트
- 네이버 서버 시간 기준으로 월요일 오전 9시에 예약 페이지를 열고
- 빈 슬롯이 생기면 자동으로 클릭 시도
"""

import asyncio
import urllib.request
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

BOOKING_URL = "https://booking.naver.com/booking/13/bizes/1432168"
KST = timezone(timedelta(hours=9))

# 추가 정보 입력값
CHILD_NAME = "신서하"
MOM_BIRTHDAY = "871019"
DAD_BIRTHDAY = "871101"

# 목표 시간: 월요일 09:00:00 KST
TARGET_TIME = datetime(2026, 4, 28, 9, 0, 0, tzinfo=KST)

# 예약 오픈 전 미리 로드할 시간(초)
PRELOAD_SECONDS = 10

# 슬롯 없을 때 재시도 간격(초)
RETRY_INTERVAL = 0.3


def get_server_time_offset() -> float:
    """네이버 서버 시간과 로컬 시간의 차이(초)를 반환. 양수 = 서버가 앞서 있음."""
    try:
        req = urllib.request.Request(
            "https://booking.naver.com/",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        local_before = datetime.now(tz=timezone.utc)
        with urllib.request.urlopen(req, timeout=3) as resp:
            local_after = datetime.now(tz=timezone.utc)
            date_header = resp.headers.get("Date")

        if not date_header:
            return 0.0

        server_time = parsedate_to_datetime(date_header)
        local_mid = local_before + (local_after - local_before) / 2
        offset = (server_time - local_mid).total_seconds()
        print(f"[시간] 서버 시간: {server_time.astimezone(KST).strftime('%H:%M:%S')} KST")
        print(f"[시간] 로컬 시간: {local_mid.astimezone(KST).strftime('%H:%M:%S')} KST")
        print(f"[시간] 오프셋: {offset:+.2f}초 (서버 기준으로 보정)")
        return offset
    except Exception as e:
        print(f"[경고] 서버 시간 조회 실패: {e} — 로컬 시간 사용")
        return 0.0


def now_server_synced(offset: float) -> datetime:
    """서버 시간 기준 현재 시각 반환."""
    return datetime.now(tz=KST) + timedelta(seconds=offset)


async def wait_until(target: datetime, offset: float):
    remaining = (target - now_server_synced(offset)).total_seconds()
    if remaining <= 0:
        return
    if remaining > PRELOAD_SECONDS + 5:
        print(f"[대기] 예약 오픈까지 {remaining:.0f}초 남음 ({target.strftime('%H:%M:%S')} KST 서버 기준)")
        await asyncio.sleep(remaining - PRELOAD_SECONDS - 5)

    # 마지막 카운트다운 (0.05초 간격)
    remaining = (target - now_server_synced(offset)).total_seconds()
    while remaining > 0:
        print(f"  → {remaining:.3f}초 후 예약 시작...", end="\r")
        await asyncio.sleep(0.02)
        remaining = (target - now_server_synced(offset)).total_seconds()
    print("\n[시작] 9시 정각! 페이지 새로고침 중...")


async def try_click_available_slot(page) -> bool:
    """영유아 검진(화요일~토요일) 예약하기 링크를 클릭. 성공하면 True 반환."""
    clicked = await page.evaluate("""
        () => {
            const links = Array.from(document.querySelectorAll('a[class*="link_desc_box"]'));
            if (links.length === 0) return 'no_links';

            for (const link of links) {
                if (link.innerText.includes('토요일')) {
                    link.click();
                    return 'clicked_saturday';
                }
            }

            links[0].click();
            return 'clicked_index0';
        }
    """)

    if clicked in ('clicked_saturday', 'clicked_index0'):
        print(f"[클릭] 예약 링크 클릭 완료! ({clicked})")
        return True

    print(f"[실패] 링크를 찾지 못함: {clicked}")
    return False


async def click_dropdown_option(page, dropdown_el, option_text: str):
    """커스텀 드롭다운을 열고 option_text가 포함된 항목 클릭."""
    await dropdown_el.click()
    await asyncio.sleep(0.3)
    # 열린 옵션 목록에서 텍스트 찾아 클릭
    clicked = await page.evaluate(f"""
        () => {{
            const targets = Array.from(document.querySelectorAll('li, option, [role="option"]'));
            const match = targets.find(el => el.innerText.trim().includes('{option_text}'));
            if (match) {{ match.click(); return true; }}
            return false;
        }}
    """)
    return clicked


async def fill_booking_form(page) -> bool:
    """추가 정보 폼 자동 입력."""
    try:
        await page.wait_for_selector('textarea, select, [class*="select"]', timeout=5000)
    except Exception:
        print("[폼] 폼 렌더링 타임아웃")
        return False

    # 드롭다운 요소 수집 (커스텀 or 네이티브 select)
    dropdowns = await page.query_selector_all('select, [class*="select_box"], [class*="SelectBox"], [class*="dropdown"]')

    # 동의/확인 선택할 드롭다운이 3개: index 0, 1, 4번째 필드
    AGREE_OPTIONS = ["동의", "확인", "예"]
    agree_idx = 0
    for dd in dropdowns:
        tag = await dd.evaluate("el => el.tagName")
        if tag == "SELECT":
            for opt in AGREE_OPTIONS:
                try:
                    await page.select_option(dd, label=opt)
                    print(f"[폼] 드롭다운 '{opt}' 선택 (native)")
                    break
                except Exception:
                    continue
        else:
            clicked = await click_dropdown_option(page, dd, AGREE_OPTIONS[agree_idx % len(AGREE_OPTIONS)])
            if clicked:
                print(f"[폼] 드롭다운 '{AGREE_OPTIONS[agree_idx % len(AGREE_OPTIONS)]}' 선택")
        agree_idx += 1
        await asyncio.sleep(0.2)

    # 텍스트 입력 (textarea 순서: 0=아이이름, 1=부모생일)
    textareas = await page.query_selector_all('textarea')
    if len(textareas) >= 1:
        await textareas[0].click()
        await textareas[0].fill(CHILD_NAME)
        print(f"[폼] 아이 이름 입력: {CHILD_NAME}")
    if len(textareas) >= 2:
        birthday_text = f"{MOM_BIRTHDAY} {DAD_BIRTHDAY}"
        await textareas[1].click()
        await textareas[1].fill(birthday_text)
        print(f"[폼] 부모 생일 입력: {birthday_text}")

    # 최종 예약 버튼 클릭
    await asyncio.sleep(0.3)
    confirmed = await page.evaluate("""
        () => {
            const buttons = Array.from(document.querySelectorAll('button'));
            const btn = buttons.find(b => b.innerText.includes('동의하고 예약하기'));
            if (btn && !btn.disabled) { btn.click(); return true; }
            return false;
        }
    """)
    if confirmed:
        print("[완료] '동의하고 예약하기' 클릭! 예약 완료!")
    else:
        print("[주의] '동의하고 예약하기' 버튼을 찾지 못함 — 수동으로 눌러주세요.")
    return True


async def select_date_and_time(page) -> bool:
    """달력에서 다음주 토요일 선택 → 첫 번째 시간 선택 → 다음 단계 클릭."""

    # 1단계: 달력 렌더링 대기
    try:
        await page.wait_for_selector('td', timeout=5000)
    except Exception:
        print("[달력] 달력 렌더링 타임아웃")
        return False

    # 2단계: 다음주 토요일 td 클릭 (토요일 = tr의 7번째 td, index 6)
    date_result = await page.evaluate("""
        () => {
            const today = new Date();
            const dayOfWeek = today.getDay();
            const daysToThisSat = (6 - dayOfWeek + 7) % 7;
            const daysToTargetSat = daysToThisSat === 0 ? 7 : daysToThisSat + 7;
            const target = new Date(today);
            target.setDate(today.getDate() + daysToTargetSat);
            const targetDate = String(target.getDate());

            // tr의 7번째 열(index 6)이 토요일
            for (const row of document.querySelectorAll('tr')) {
                const cells = row.querySelectorAll('td');
                if (cells.length < 7) continue;
                const satCell = cells[6];
                if (satCell.innerText.trim() !== targetDate) continue;
                const link = satCell.querySelector('a');
                if (link) { link.click(); return { ok: true, date: targetDate, via: 'a' }; }
                satCell.click();
                return { ok: true, date: targetDate, via: 'td' };
            }
            return { ok: false, date: targetDate };
        }
    """)

    if not date_result['ok']:
        print(f"[달력] 날짜 {date_result['date']}일을 찾지 못함 (달력에 없거나 비활성)")
        return False
    print(f"[달력] {date_result['date']}일(토) 클릭! (via {date_result['via']})")

    # 3단계: 시간 슬롯 렌더링 대기 후 첫 번째 시간 클릭
    await asyncio.sleep(0.5)
    time_result = await page.evaluate("""
        () => {
            const slots = Array.from(document.querySelectorAll('button, a, li'))
                .filter(el => /^\d{2}:\d{2}$/.test(el.innerText.trim()) && !el.disabled);
            if (!slots.length) return null;
            slots[0].click();
            return slots[0].innerText.trim();
        }
    """)

    if not time_result:
        print("[시간] 시간 슬롯을 찾지 못함")
        return False
    print(f"[시간] {time_result} 선택!")

    # 4단계: 다음 단계 버튼 클릭
    await asyncio.sleep(0.3)
    next_clicked = await page.evaluate("""
        () => {
            const btn = document.querySelector('button[class*="btn_next"]');
            if (btn && !btn.disabled) { btn.click(); return true; }
            return false;
        }
    """)

    if next_clicked:
        print("[다음] '다음 단계' 클릭!")
        return True

    print("[다음] '다음 단계' 버튼을 찾지 못함")
    return False


async def main(test: bool = False):
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("[오류] Playwright가 설치되어 있지 않습니다.")
        print("아래 명령어로 설치하세요:")
        print("  pip install playwright")
        print("  playwright install chromium")
        return

    if test:
        print("=" * 40)
        print("  테스트 모드: 대기 없이 바로 클릭 시도")
        print("=" * 40)

    # 서버 시간 오프셋 측정
    offset = get_server_time_offset()

    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=r"C:\Users\shin2\Documents\naver_chrome_profile",
            headless=False,
            no_viewport=True,
        )

        page = await browser.new_page()

        # --- 1단계: 예약 페이지 미리 열기 ---
        print(f"\n[준비] 예약 페이지 로딩: {BOOKING_URL}")
        await page.goto(BOOKING_URL, wait_until="domcontentloaded")
        print("[준비] 페이지 로드 완료.\n")

        if not test:
            print("네이버 로그인이 안 되어 있으면 지금 로그인하세요!")
            # --- 2단계: 서버 시간 기준 9시까지 대기 ---
            await wait_until(TARGET_TIME, offset)

        # --- 3단계: 새로고침 + 슬롯 자동 클릭 시도 ---
        attempt = 0
        while True:
            attempt += 1
            print(f"[시도 {attempt}] 페이지 새로고침...")
            try:
                await page.reload(wait_until="domcontentloaded", timeout=5000)
            except Exception:
                pass

            # SPA 렌더링 완료 대기 — 링크가 DOM에 나타날 때까지
            try:
                await page.wait_for_selector('a[class*="link_desc_box"]', timeout=5000)
            except Exception:
                print("[대기] 링크 렌더링 타임아웃, 그냥 시도...")

            clicked = await try_click_available_slot(page)
            if clicked:
                # 달력 페이지로 이동 대기
                await asyncio.sleep(1)
                date_ok = await select_date_and_time(page)
                if date_ok:
                    await asyncio.sleep(1.5)  # 다음 단계 페이지 로드 대기
                    await fill_booking_form(page)
                else:
                    print("[주의] 날짜/시간 자동 선택 실패 — 수동으로 진행하세요.")
                await asyncio.sleep(120)
                break

            if test:
                print("[테스트] 클릭 실패 — 셀렉터가 맞지 않습니다.")
                await asyncio.sleep(30)
                break

            print(f"[대기] 슬롯 없음. {RETRY_INTERVAL}초 후 재시도...")
            await asyncio.sleep(RETRY_INTERVAL)

            if attempt >= 30:
                print("[알림] 30회 시도 후 슬롯을 찾지 못했습니다. 수동으로 진행하세요.")
                await asyncio.sleep(120)
                break

        await browser.close()


if __name__ == "__main__":
    import sys
    TEST_MODE = "--test" in sys.argv
    asyncio.run(main(test=TEST_MODE))
