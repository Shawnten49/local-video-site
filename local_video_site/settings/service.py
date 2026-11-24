from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

from .errors import SettingError
from .registry import (
    CATEGORY_LABELS,
    DEFINITIONS_BY_KEY,
    SETTING_DEFINITIONS,
    SettingDefinition,
    iter_definitions,
    validate_value,
)


def _format_display(value: object, definition: SettingDefinition) -> str:
    if definition.secret:
        return "已配置" if value is True else "未配置"
    if value is None:
        return "—"
    if definition.value_type == "bool":
        return "是" if bool(value) else "否"
    if definition.value_type == "bytes":
        return "{} B".format(int(value))
    if definition.unit:
        return "{} {}".format(value, definition.unit)
    return str(value)


class SettingsService:
    def __init__(self) -> None:
        self._items: Dict[str, Dict] = {}
        self._loaded = False
        self._environment_state = self._current_environment_state()

    @staticmethod
    def _current_environment_state() -> tuple:
        return tuple(
            (item.env_name, os.environ.get(item.env_name))
            for item in SETTING_DEFINITIONS
            if item.env_name
        )

    def refresh(self) -> None:
        from local_video_site.repositories import settings as repository

        try:
            overrides = repository.load_overrides()
        except repository.SettingsStorageNotReady:
            overrides = {}
            loaded = False
        else:
            loaded = True
        items: Dict[str, Dict] = {}
        for definition in SETTING_DEFINITIONS:
            items[definition.key] = self._resolve_item(
                definition,
                overrides.get(definition.key),
            )
        self._items = items
        self._loaded = loaded
        self._environment_state = self._current_environment_state()

    def _ensure_loaded(self) -> None:
        if (
            not self._loaded
            or self._environment_state != self._current_environment_state()
        ):
            self.refresh()

    def _resolve_item(
        self,
        definition: SettingDefinition,
        override: Optional[Dict],
    ) -> Dict:
        if definition.value_type == "static":
            return self._static_item(definition)
        if definition.secret:
            return self._secret_item(definition)
        value = definition.default
        source = "default"
        version = 0
        updated_at = ""
        updated_by = ""
        note = ""
        invalid = False
        env_name = definition.env_name or ""
        env_raw = os.environ.get(env_name) if env_name else None
        env_locked = env_raw is not None
        if env_locked:
            try:
                value = validate_value(definition, env_raw)
                source = "environment"
            except (TypeError, ValueError) as exc:
                raise SettingError(
                    "环境变量 {} 无效: {}".format(env_name, exc),
                    "setting_environment_invalid",
                    500,
                ) from exc
        elif override:
            version = int(override.get("version") or 0)
            updated_at = str(override.get("updated_at") or "")
            updated_by = str(override.get("updated_by_username") or "")
            note = str(override.get("note") or "")
            try:
                value = validate_value(definition, override.get("value"))
                source = "database"
            except (TypeError, ValueError):
                value = definition.default
                source = "default_after_invalid_override"
                invalid = True
        return {
            "key": definition.key,
            "label": definition.label,
            "category": definition.category,
            "category_label": CATEGORY_LABELS.get(
                definition.category,
                definition.category,
            ),
            "value_type": definition.value_type,
            "value": value,
            "display_value": _format_display(value, definition),
            "default_value": definition.default,
            "default_display": _format_display(
                definition.default,
                definition,
            ),
            "description": definition.description,
            "env_name": env_name,
            "env_locked": env_locked,
            "source": source,
            "version": version,
            "updated_at": updated_at,
            "updated_by_username": updated_by,
            "note": note,
            "apply_mode": definition.apply_mode,
            "unit": definition.unit,
            "min_value": definition.min_value,
            "max_value": definition.max_value,
            "choices": list(definition.choices),
            "dangerous": definition.dangerous,
            "advanced": definition.advanced,
            "secret": False,
            "invalid_override": invalid,
            "mutable": bool(definition.mutable and not env_locked),
        }

    def _static_item(self, definition: SettingDefinition) -> Dict:
        from local_video_site.core import config

        values = {
            "system.database_path": config.DATABASE_PATH,
            "system.data_dir": config.DATA_DIR,
            "system.thumbnail_dir": config.THUMBNAIL_DIR,
            "system.cover_dir": config.COVER_DIR,
            "system.preview_dir": config.PREVIEW_DIR,
            "system.playback_cache_dir": config.playback_cache_dir(),
            "system.lan_mode": config.lan_mode(),
        }
        value = values.get(definition.key, "")
        if isinstance(value, Path):
            value = str(value)
        display = (
            "是" if value is True else "否"
            if definition.key == "system.lan_mode"
            else str(value)
        )
        return {
            "key": definition.key,
            "label": definition.label,
            "category": definition.category,
            "category_label": CATEGORY_LABELS.get(
                definition.category,
                definition.category,
            ),
            "value_type": "static",
            "value": value,
            "display_value": display,
            "default_value": value,
            "default_display": display,
            "description": definition.description,
            "env_name": definition.env_name or "",
            "env_locked": True,
            "source": "startup",
            "version": 0,
            "updated_at": "",
            "updated_by_username": "",
            "note": "",
            "apply_mode": definition.apply_mode,
            "unit": definition.unit,
            "min_value": None,
            "max_value": None,
            "choices": [],
            "dangerous": False,
            "advanced": False,
            "secret": False,
            "invalid_override": False,
            "mutable": False,
        }

    def _secret_item(self, definition: SettingDefinition) -> Dict:
        from local_video_site.storage.deletion import delete_password_configured

        configured = delete_password_configured()
        return {
            "key": definition.key,
            "label": definition.label,
            "category": definition.category,
            "category_label": CATEGORY_LABELS.get(
                definition.category,
                definition.category,
            ),
            "value_type": "secret",
            "value": None,
            "display_value": "已配置" if configured else "未配置",
            "default_value": None,
            "default_display": "未配置",
            "description": definition.description,
            "env_name": definition.env_name or "",
            "env_locked": False,
            "source": "secret_file" if configured else "unconfigured",
            "version": 0,
            "updated_at": "",
            "updated_by_username": "",
            "note": "",
            "apply_mode": "secret",
            "unit": "",
            "min_value": DEFINITIONS_BY_KEY[
                "security.delete_verification_min_length"
            ].default,
            "max_value": 128,
            "choices": [],
            "dangerous": True,
            "advanced": False,
            "secret": True,
            "configured": configured,
            "invalid_override": False,
            "mutable": True,
        }

    def get_item(self, key: str) -> Dict:
        self._ensure_loaded()
        item = self._items.get(str(key))
        if item is None:
            raise SettingError(
                "参数不存在",
                "setting_not_found",
                404,
            )
        return dict(item)

    def get(self, key: str) -> object:
        item = self.get_item(key)
        if item["secret"]:
            raise SettingError(
                "敏感参数不能直接读取",
                "setting_secret_not_readable",
                409,
            )
        return item["value"]

    def get_int(self, key: str) -> int:
        return int(self.get(key))

    def get_float(self, key: str) -> float:
        return float(self.get(key))

    def get_bool(self, key: str) -> bool:
        return bool(self.get(key))

    def get_string(self, key: str) -> str:
        return str(self.get(key))

    def list_items(
        self,
        category: str = "",
        query: str = "",
        include_advanced: bool = True,
    ) -> List[Dict]:
        self._ensure_loaded()
        return [
            dict(self._items[definition.key])
            for definition in iter_definitions(
                category=category,
                query=query,
                include_advanced=include_advanced,
            )
        ]

    def categories(self) -> List[Dict]:
        counts: Dict[str, int] = {}
        for definition in SETTING_DEFINITIONS:
            counts[definition.category] = counts.get(
                definition.category,
                0,
            ) + 1
        return [
            {
                "key": key,
                "label": CATEGORY_LABELS.get(key, key),
                "count": counts.get(key, 0),
            }
            for key in CATEGORY_LABELS
            if counts.get(key)
        ]

    def _definition(self, key: str) -> SettingDefinition:
        definition = DEFINITIONS_BY_KEY.get(str(key))
        if definition is None:
            raise SettingError(
                "参数不存在",
                "setting_not_found",
                404,
            )
        return definition

    def _check_writable(self, definition: SettingDefinition) -> None:
        if not definition.mutable:
            raise SettingError(
                "该参数属于启动级配置，只能通过命令行或环境变量修改",
                "setting_read_only",
                409,
            )
        if definition.secret:
            raise SettingError(
                "敏感参数必须通过专用接口修改",
                "setting_secret_endpoint_required",
                409,
            )
        if definition.env_name and os.environ.get(definition.env_name) is not None:
            raise SettingError(
                "该参数已被环境变量锁定",
                "setting_env_locked",
                409,
            )

    def update(
        self,
        key: str,
        value: object,
        *,
        expected_version: int,
        actor_user_id: Optional[int],
        actor_username: str,
        client_ip: str = "",
        request_id: str = "",
        note: str = "",
    ) -> Dict:
        from local_video_site.repositories import settings as repository

        definition = self._definition(key)
        self._check_writable(definition)
        try:
            parsed = validate_value(definition, value)
        except (TypeError, ValueError) as exc:
            raise SettingError(
                "{}: {}".format(definition.label, exc),
                "setting_validation_failed",
                422,
            ) from exc
        current = self.get_item(definition.key)
        repository.write_override(
            setting_key=definition.key,
            value=parsed,
            value_type=definition.value_type,
            expected_version=int(expected_version),
            actor_user_id=actor_user_id,
            actor_username=actor_username,
            client_ip=client_ip,
            request_id=request_id,
            note=note,
            old_display=str(current.get("display_value") or ""),
            new_display=_format_display(parsed, definition),
        )
        self.refresh()
        return self.get_item(definition.key)

    def reset(
        self,
        key: str,
        *,
        expected_version: int,
        actor_user_id: Optional[int],
        actor_username: str,
        client_ip: str = "",
        request_id: str = "",
        note: str = "",
    ) -> Dict:
        from local_video_site.repositories import settings as repository

        definition = self._definition(key)
        self._check_writable(definition)
        current = self.get_item(definition.key)
        if current["source"] != "database":
            raise SettingError(
                "参数没有数据库覆盖值",
                "setting_override_missing",
                409,
            )
        repository.delete_override(
            setting_key=definition.key,
            expected_version=int(expected_version),
            actor_user_id=actor_user_id,
            actor_username=actor_username,
            client_ip=client_ip,
            request_id=request_id,
            note=note,
            old_display=str(current.get("display_value") or ""),
            new_display=_format_display(definition.default, definition),
        )
        self.refresh()
        return self.get_item(definition.key)

    def change_delete_verification(
        self,
        *,
        current_password: str,
        new_password: str,
        confirmation: str,
        actor_user_id: Optional[int],
        actor_username: str,
        client_ip: str = "",
        request_id: str = "",
        note: str = "",
    ) -> Dict:
        from local_video_site.repositories import settings as repository
        from local_video_site.storage.deletion import (
            delete_password_configured,
            set_delete_password,
            verify_delete_password,
        )

        configured = delete_password_configured()
        if configured and not verify_delete_password(current_password):
            raise SettingError(
                "当前删除校验码错误",
                "delete_verification_invalid",
                403,
            )
        if str(new_password or "") != str(confirmation or ""):
            raise SettingError(
                "两次输入的新校验码不一致",
                "delete_verification_confirmation",
                422,
            )
        minimum = self.get_int(
            "security.delete_verification_min_length"
        )
        if len(str(new_password or "")) < minimum:
            raise SettingError(
                "删除校验码至少需要 {} 个字符".format(minimum),
                "delete_verification_too_short",
                422,
            )
        set_delete_password(new_password)
        repository.record_secret_audit(
            setting_key="security.delete_verification",
            action="secret_change",
            actor_user_id=actor_user_id,
            actor_username=actor_username,
            client_ip=client_ip,
            request_id=request_id,
            note=note,
            old_display="已配置" if configured else "未配置",
            new_display="已配置",
        )
        self.refresh()
        return self.get_item("security.delete_verification")

    def audit(
        self,
        setting_key: str = "",
        page: int = 1,
        page_size: int = 30,
    ) -> Dict:
        from local_video_site.repositories import settings as repository

        if setting_key:
            self._definition(setting_key)
        return repository.list_audit(
            setting_key=setting_key,
            page=page,
            page_size=page_size,
        )
