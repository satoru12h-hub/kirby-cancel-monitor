"""カービィカフェ7月分の予約フォームを、確保済み枠から完了まで進める。"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re

from playwright.sync_api import TimeoutError as PWTimeout


JULY_2026_PREFIX = "2026-07-"


@dataclass(frozen=True)
class BookingConfig:
    name_last: str
    name_first: str
    kana_last: str
    kana_first: str
    mobile: str
    mobile_fallback: str
    email: str
    privacy_consent: str


@dataclass(frozen=True)
class BookingResult:
    status: str  # success / lost / error
    code: str
    start_at: str = ""
    used_mobile_fallback: bool = False


def is_enabled() -> bool:
    return os.environ.get("KIRBY_AUTO_BOOK_ENABLED", "").strip().lower() == "true"


def load_config() -> BookingConfig:
    return BookingConfig(
        name_last=os.environ.get("KIRBY_NAME_LAST", "").strip(),
        name_first=os.environ.get("KIRBY_NAME_FIRST", "").strip(),
        kana_last=os.environ.get("KIRBY_KANA_LAST", "").strip(),
        kana_first=os.environ.get("KIRBY_KANA_FIRST", "").strip(),
        mobile=os.environ.get("KIRBY_MOBILE", "").strip(),
        mobile_fallback=os.environ.get("KIRBY_MOBILE_FALLBACK", "").strip(),
        email=os.environ.get("KIRBY_EMAIL", "").strip(),
        privacy_consent=os.environ.get("KIRBY_PRIVACY_CONSENT", "").strip(),
    )


def validate_config(config: BookingConfig) -> list[str]:
    values = {
        "KIRBY_NAME_LAST": config.name_last,
        "KIRBY_NAME_FIRST": config.name_first,
        "KIRBY_KANA_LAST": config.kana_last,
        "KIRBY_KANA_FIRST": config.kana_first,
        "KIRBY_MOBILE": config.mobile,
        "KIRBY_EMAIL": config.email,
    }
    errors = [name for name, value in values.items() if not value]
    if config.mobile and not re.fullmatch(r"\d{10,13}", config.mobile):
        errors.append("KIRBY_MOBILE_FORMAT")
    if config.mobile_fallback and not re.fullmatch(r"\d{10,13}", config.mobile_fallback):
        errors.append("KIRBY_MOBILE_FALLBACK_FORMAT")
    if config.email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", config.email):
        errors.append("KIRBY_EMAIL_FORMAT")
    if config.privacy_consent != "YES":
        errors.append("KIRBY_PRIVACY_CONSENT")
    return errors


def complete_booking(page, slot_link, start_at: str, config: BookingConfig) -> BookingResult:
    """既に見つけた○リンクをクリックし、同一ブラウザで予約確定まで進める。"""
    if not start_at.startswith(JULY_2026_PREFIX):
        return BookingResult("error", "outside_july_2026")

    held = False
    try:
        slot_link.click()
        try:
            page.wait_for_selector(".v-dialog--active", state="visible", timeout=15000)
        except PWTimeout:
            return BookingResult("lost", "hold_failed")
        held = True

        fields = (
            ("姓", config.name_last),
            ("名", config.name_first),
            ("セイ", config.kana_last),
            ("メイ", config.kana_first),
            ("電話番号", config.mobile),
            ("メールアドレス", config.email),
        )
        for label, value in fields:
            locator = page.get_by_label(label, exact=True)
            if locator.count() != 1:
                raise RuntimeError(f"field_not_unique:{label}")
            locator.fill(value)

        no_birthday = page.get_by_label("希望しない", exact=True)
        if no_birthday.count() != 1:
            raise RuntimeError("birthday_option_not_unique")
        no_birthday.check()

        check_button = page.get_by_role("button", name="入力内容を確認", exact=True)
        if check_button.count() != 1:
            raise RuntimeError("check_button_not_unique")
        confirm_button = page.get_by_role("button", name="予約確定", exact=True)
        used_mobile_fallback = False
        check_button.click()
        try:
            confirm_button.wait_for(state="visible", timeout=10000)
        except PWTimeout:
            # 国際番号等が予約フォームの検証で弾かれた場合に限り、予備番号で再検証する。
            if not config.mobile_fallback or config.mobile_fallback == config.mobile:
                raise RuntimeError("form_validation_failed")
            mobile = page.get_by_label("電話番号", exact=True)
            if mobile.count() != 1:
                raise RuntimeError("mobile_field_not_unique")
            mobile.fill(config.mobile_fallback)
            used_mobile_fallback = True
            check_button.click()
            confirm_button.wait_for(state="visible", timeout=10000)

        policy = page.locator(".v-dialog--active .overflow-y-auto")
        if policy.count() != 1:
            raise RuntimeError("privacy_policy_not_unique")
        policy.evaluate("el => { el.scrollTop = el.scrollHeight; el.dispatchEvent(new Event('scroll')); }")

        consent = page.get_by_label("個人情報の取扱いに同意する", exact=True)
        consent.wait_for(state="visible", timeout=5000)
        consent.check()

        if not confirm_button.is_enabled():
            raise RuntimeError("confirm_button_disabled")
        confirm_button.click()
        page.wait_for_url("**/reserve/done", timeout=30000)
        held = False
        return BookingResult("success", "confirmed", start_at, used_mobile_fallback)
    except Exception as exc:
        # 個人情報やページ本文はログに出さず、安全なエラー種別だけ返す。
        code = str(exc).split(":", 1)[0] if isinstance(exc, RuntimeError) else type(exc).__name__
        if held:
            _release_hold(page)
        return BookingResult("error", code)


def _release_hold(page) -> None:
    """入力途中で失敗した場合、可能なら枠を即時解放する。"""
    try:
        cancel = page.get_by_role("button", name="キャンセル", exact=True)
        if cancel.count() == 1 and cancel.is_visible():
            cancel.click()
            page.wait_for_timeout(500)
    except Exception:
        # 解放操作も失敗した場合はブラウザ終了によるタイムアウト解放に任せる。
        pass
