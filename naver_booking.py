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
    """두상 교정 예약하기 링크를 클릭. 성공하면 True 반환."""
    # '두상' 텍스트 포함 링크 찾기
    selector = await page.evaluate("""
        () => {
            const links = Array.from(document.querySelectorAll('a[class*="link_desc_box"]'));
            if (!links.length) return null;
            const dusang = links.find(l => l.innerText.includes('두상'));
            if (dusang) {
                // 고유 식별용 index 반환
                return links.indexOf(dusang);
            }
            return links.length > 2 ? 2 : 0;
        }
    """)
    if selector is None:
        print("[실패] 링크를 찾지 못함")
        return False
    try:
        links = await page.query_selector_all('a[class*="link_desc_box"]')
        await links[selector].click()
        print(f"[클릭] 예약 링크 클릭 완료! (index {selector})")
        return True
    except Exception as e:
        print(f"[실패] 링크 클릭 실패: {e}")
        return False


async def fill_booking_form(page) -> bool:
    """추가 정보 폼 자동 입력."""
    try:
        await page.wait_for_selector('button.select_btn', timeout=5000)
    except Exception:
        print("[폼] 폼 렌더링 타임아웃")
        return False

    # 드롭다운: button.select_btn 클릭 → 열린 옵션에서 동의/확인 클릭
    select_btns = await page.query_selector_all('button.select_btn')
    for btn in select_btns:
        await btn.click()
        await asyncio.sleep(0.3)
        # 열린 옵션 목록에서 동의/확인/예 찾아 클릭 (button.select_item)
        clicked = await btn.evaluate("""
            el => {
                const container = el.closest('.form_select');
                if (!container) return null;
                const options = Array.from(container.querySelectorAll('button.select_item'));
                const match = options.find(b => {
                    const t = b.innerText.trim();
                    return t === '동의' || t === '확인' || t === '예';
                });
                if (match) { match.click(); return match.innerText.trim(); }
                return null;
            }
        """)
        if clicked:
            print(f"[폼] 드롭다운 '{clicked}' 선택")
        else:
            print("[폼] 드롭다운 옵션을 찾지 못함")
        await asyncio.sleep(0.2)

    # 텍스트 입력 (textarea 순서: 0=아이이름, 1=부모생일)
    textareas = await page.query_selector_all('textarea')
    if len(textareas) >= 1:
        await textareas[0].click()
        await textareas[0].fill(CHILD_NAME)
        print(f"[폼] 아이 이름 입력: {CHILD_NAME}")
    if len(textareas) >= 2:
        birthday_text = f"아빠({DAD_BIRTHDAY}) 엄마({MOM_BIRTHDAY})"
        await textareas[1].click()
        await textareas[1].fill(birthday_text)
        print(f"[폼] 부모 생일 입력: {birthday_text}")

    # 최종 예약 버튼 클릭
    await asyncio.sleep(0.3)
    try:
        await page.click('button:has-text("동의하고 예약하기")')
        confirmed = True
    except Exception:
        confirmed = False
    if confirmed:
        print("[완료] '동의하고 예약하기' 클릭! 예약 완료!")
    else:
        print("[주의] '동의하고 예약하기' 버튼을 찾지 못함 — 수동으로 눌러주세요.")
    return True


async def select_date_and_time(page) -> bool:
    """달력에서 가장 빠른 날짜/시간 선택 → 두상 교정 체크박스 선택 → 다음단계 클릭."""

    # 1단계: 달력 렌더링 대기
    try:
        await page.wait_for_selector('td', timeout=5000)
    except Exception:
        print("[달력] 달력 렌더링 타임아웃")
        return False

    # 2단계: 가능한 날짜 찾기 — 없으면 '>' 눌러 다음 달로 (최대 6달)
    date_result = {'ok': False}
    for month_try in range(6):
        date_result = await page.evaluate("""
            () => {
                const dates = Array.from(document.querySelectorAll('[class*="calendar_date"]'))
                    .filter(el => el.className.trim() === 'calendar_date');
                if (dates.length > 0) {
                    dates[0].click();
                    return { ok: true, date: dates[0].innerText.trim() };
                }
                return { ok: false };
            }
        """)
        if date_result['ok']:
            break
        # 이번 달에 가능한 날짜 없음 → '>' 버튼 클릭
        next_clicked = await page.evaluate("""
            () => {
                const btns = Array.from(document.querySelectorAll('button'));
                const next = btns.find(b =>
                    b.innerText.trim() === '>' ||
                    b.className.includes('next') ||
                    b.className.includes('Next') ||
                    b.getAttribute('aria-label') === '다음달'
                );
                if (next) { next.click(); return true; }
                return false;
            }
        """)
        if not next_clicked:
            print("[달력] 다음 달 버튼을 찾지 못함")
            break
        print(f"[달력] 이번 달 예약 없음 → 다음 달로 이동 ({month_try + 1}회)")
        await asyncio.sleep(0.5)

    if not date_result['ok']:
        print("[달력] 선택 가능한 날짜를 찾지 못함")
        return False
    print(f"[달력] {date_result['date']}일 클릭!")

    # 3단계: 시간 슬롯 렌더링 대기 후 첫 번째 시간 클릭
    await asyncio.sleep(0.5)
    slots = await page.query_selector_all('button.btn_time, a.btn_time, li.btn_time')
    # className이 정확히 'btn_time'인 것만
    time_result = None
    for slot in slots:
        cls = await slot.evaluate('el => el.className.trim()')
        if cls == 'btn_time':
            await slot.click()
            time_result = await slot.inner_text()
            break

    if not time_result:
        print("[시간] 시간 슬롯을 찾지 못함")
        return False
    print(f"[시간] {time_result} 선택!")

    # 4단계: '두상 교정' 체크박스 선택
    await asyncio.sleep(0.3)
    try:
        icon = await page.query_selector(
            '[data-click-code="options.checkbookingcount0"] ~ span.ico_check'
        )
        if not icon:
            icon = await page.query_selector('[data-click-code="options.checkbookingcount0"]')
            icon = await icon.evaluate_handle('el => el.closest(".checkbox_icon")')
        await icon.click()
        print("[체크] 두상 교정 체크박스 클릭!")
    except Exception as e:
        print(f"[체크] 클릭 실패: {e}")

    # 5단계: 다음단계 버튼 클릭
    await asyncio.sleep(0.8)
    btn = await page.query_selector('button.NextButton__btn_next__kfLFW')
    if not btn:
        print("[다음] 버튼을 찾지 못함")
        return False
    await btn.scroll_into_view_if_needed()
    await asyncio.sleep(0.3)
    try:
        await btn.click()
        print("[다음] '다음단계' 클릭!")
        return True
    except Exception as e:
        print(f"[다음] 클릭 실패: {e}")
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
