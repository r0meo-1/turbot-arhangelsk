"""152-ФЗ consent and privacy notice text builders."""

from __future__ import annotations


def consent_text(
    operator_name: str,
    privacy_policy_url: str = "",
    erase_hint: str = "командой /delete",
) -> str:
    """Build the personal-data consent prompt shown before data collection."""
    policy_line = (
        f"\n📄 Полный текст: {privacy_policy_url}\n" if privacy_policy_url else "\n"
    )
    return (
        "🔒 Перед подбором тура нужно ваше согласие на обработку персональных данных.\n\n"
        f"Оператор: {operator_name}.\n\n"
        "Нажимая «Согласен», вы даёте согласие на обработку ваших имени и номера "
        "телефона с целью подбора тура и связи с вами (ст. 6, 9 ФЗ-152)."
        + policy_line
        + f"Вы вправе отозвать согласие и удалить данные {erase_hint}."
    )


def privacy_text(
    operator_name: str,
    platform_id_label: str,
    privacy_policy_url: str = "",
    retention_days: int = 180,
    erase_hint: str = "команда /delete",
) -> str:
    """Short privacy notice for the privacy command."""
    lines = [
        "🔒 Обработка персональных данных\n",
        f"Оператор: {operator_name}.",
        "Цель: подбор тура и связь с клиентом.",
        f"Обрабатываемые данные: имя, номер телефона, идентификатор {platform_id_label}.",
    ]
    if retention_days > 0:
        lines.append(
            f"Срок хранения: до {retention_days} дней после обращения, "
            "затем автоматическое удаление."
        )
    lines.append(f"Права: отозвать согласие и удалить данные — {erase_hint}.")
    if privacy_policy_url:
        lines.append(f"\n📄 Полный текст: {privacy_policy_url}")
    return "\n".join(lines)
