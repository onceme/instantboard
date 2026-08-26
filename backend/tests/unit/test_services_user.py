"""Unit tests for user preferences (services/user.py): favorite_tags normalization
(lowercase / pattern check / de-dup / cap) and the UserService read/update paths."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import InvalidToken, ValidationError
from app.schemas.user import MAX_FAVORITE_TAGS
from app.services.user import UserService, normalize_favorite_tags

# Valid UUID hex strings (UserService parses token ids with uuid.UUID)
USER_ID = "0" * 32
TENANT_ID = "1" * 32


class TestNormalizeFavoriteTags:
    def test_lowercases_tags(self):
        assert normalize_favorite_tags(["LLM", "Drone"]) == ["llm", "drone"]

    def test_dedupes_case_insensitively_keeping_first_seen_order(self):
        assert normalize_favorite_tags(["LLM", "drone", "llm", "DRONE"]) == ["llm", "drone"]

    def test_accepts_digits_and_hyphens(self):
        assert normalize_favorite_tags(["risc-v", "iot-edge", "3d-printing"]) == ["risc-v", "iot-edge", "3d-printing"]

    def test_empty_list_is_ok(self):
        assert normalize_favorite_tags([]) == []

    def test_max_length_boundary(self):
        assert normalize_favorite_tags(["a" * 32]) == ["a" * 32]

    @pytest.mark.parametrize(
        "bad_tag",
        ["a" * 33, "llm_gpt", "bad tag", "llm!", "中文标签", ""],
    )
    def test_invalid_tag_raises_validation_error(self, bad_tag):
        with pytest.raises(ValidationError) as exc_info:
            normalize_favorite_tags(["llm", bad_tag])
        details = exc_info.value.error_details
        assert details[0]["field"] == "favorite_tags"
        assert "^[a-z0-9-]{1,32}$" in details[0]["message"]

    def test_invalid_tags_are_listed_in_details(self):
        with pytest.raises(ValidationError) as exc_info:
            normalize_favorite_tags(["Bad_One", "bad two", "llm"])
        message = exc_info.value.error_details[0]["message"]
        assert "Bad_One" in message
        assert "bad two" in message

    def test_too_many_tags_raises_validation_error(self):
        tags = [f"tag-{i}" for i in range(MAX_FAVORITE_TAGS + 1)]
        with pytest.raises(ValidationError) as exc_info:
            normalize_favorite_tags(tags)
        details = exc_info.value.error_details
        assert details[0]["field"] == "favorite_tags"
        assert str(MAX_FAVORITE_TAGS) in details[0]["message"]

    def test_exactly_max_tags_is_ok(self):
        tags = [f"tag-{i}" for i in range(MAX_FAVORITE_TAGS)]
        assert normalize_favorite_tags(tags) == tags

    def test_duplicates_collapse_below_cap(self):
        # 60 raw entries collapsing to 30 unique ones pass the cap check.
        tags = [f"tag-{i % 30}" for i in range(60)]
        assert len(normalize_favorite_tags(tags)) == 30


def _mock_db(user=None):
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db.execute = AsyncMock(return_value=result)
    return db


def _mock_user(preferences=None):
    user = MagicMock()
    user.preferences = preferences
    return user


class TestUpdatePreferences:
    async def test_echoes_normalized_tags_and_commits(self):
        user = _mock_user(preferences={})
        db = _mock_db(user)
        service = UserService(db)

        result = await service.update_preferences(USER_ID, TENANT_ID, ["LLM", "llm", "Drone"])

        assert result == {"favorite_tags": ["llm", "drone"]}
        assert user.preferences == {"favorite_tags": ["llm", "drone"]}
        db.commit.assert_awaited_once()

    async def test_merges_into_existing_preferences_keeps_other_keys(self):
        user = _mock_user(preferences={"theme": "dark", "favorite_tags": ["old"]})
        db = _mock_db(user)
        service = UserService(db)

        result = await service.update_preferences(USER_ID, TENANT_ID, ["llm"])

        assert result == {"favorite_tags": ["llm"]}
        assert user.preferences == {"theme": "dark", "favorite_tags": ["llm"]}

    async def test_invalid_tags_raise_and_skip_commit(self):
        user = _mock_user(preferences={})
        db = _mock_db(user)
        service = UserService(db)

        with pytest.raises(ValidationError):
            await service.update_preferences(USER_ID, TENANT_ID, ["bad tag"])

        db.commit.assert_not_awaited()
        assert user.preferences == {}

    async def test_missing_user_raises_invalid_token(self):
        db = _mock_db(user=None)
        service = UserService(db)

        with pytest.raises(InvalidToken):
            await service.update_preferences(USER_ID, TENANT_ID, ["llm"])


class TestGetPreferences:
    async def test_returns_stored_tags(self):
        db = _mock_db(_mock_user(preferences={"favorite_tags": ["llm", "drone"]}))
        service = UserService(db)

        result = await service.get_preferences(USER_ID, TENANT_ID)
        assert result == {"favorite_tags": ["llm", "drone"]}

    async def test_missing_preferences_yields_empty_list(self):
        db = _mock_db(_mock_user(preferences={}))
        service = UserService(db)

        result = await service.get_preferences(USER_ID, TENANT_ID)
        assert result == {"favorite_tags": []}

    async def test_non_list_favorite_tags_are_ignored(self):
        db = _mock_db(_mock_user(preferences={"favorite_tags": "llm"}))
        service = UserService(db)

        result = await service.get_preferences(USER_ID, TENANT_ID)
        assert result == {"favorite_tags": []}

    async def test_non_string_entries_are_filtered(self):
        db = _mock_db(_mock_user(preferences={"favorite_tags": ["llm", 1, None]}))
        service = UserService(db)

        result = await service.get_preferences(USER_ID, TENANT_ID)
        assert result == {"favorite_tags": ["llm"]}

    async def test_missing_user_raises_invalid_token(self):
        db = _mock_db(user=None)
        service = UserService(db)

        with pytest.raises(InvalidToken):
            await service.get_preferences(USER_ID, TENANT_ID)


class TestGetUserPreferences:
    async def test_returns_preferences_blob(self):
        prefs = {"favorite_tags": ["llm"]}
        db = _mock_db(_mock_user(preferences=prefs))
        service = UserService(db)

        assert await service.get_user_preferences("0" * 32, "1" * 32) == prefs

    async def test_missing_user_returns_none(self):
        db = _mock_db(user=None)
        service = UserService(db)

        assert await service.get_user_preferences("0" * 32, "1" * 32) is None

    async def test_malformed_ids_return_none(self):
        db = _mock_db(_mock_user(preferences={}))
        service = UserService(db)

        assert await service.get_user_preferences("not-a-uuid", "1" * 32) is None
        assert await service.get_user_preferences(None, None) is None
