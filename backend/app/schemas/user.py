from __future__ import annotations

from pydantic import BaseModel, Field

# favorite_tags 的取值约束与 content-categories.md §3.6.1 的手动打标规则一致：
# 小写字母/数字/连字符，长度 1-32。校验与归一化在 services/user.py 中进行
# （需要返回 400 VALIDATION_ERROR，而 Pydantic field_validator 只能产生 422）。
FAVORITE_TAG_PATTERN = r"^[a-z0-9-]{1,32}$"
MAX_FAVORITE_TAGS = 50


class UserPreferencesUpdate(BaseModel):
    # Required (PUT is a whole-preferences replacement): an omitted field is a
    # malformed request, not "clear the tags" (an explicit empty list does that).
    favorite_tags: list[str]


class UserPreferencesResponse(BaseModel):
    favorite_tags: list[str] = Field(default_factory=list)
