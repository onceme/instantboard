#!/usr/bin/env bash
#
# sync-wiki.sh — 将 docs/ 目录同步为 GitHub Wiki（单向：docs/ 是唯一事实来源）
#
# 用法：
#   export WIKI_TOKEN=<具备 wiki 写权限的 PAT 或部署密钥>
#   bash scripts/sync-wiki.sh
#
# 环境变量：
#   WIKI_TOKEN   必需（除非显式提供 WIKI_REPO）。默认 GITHUB_TOKEN 对 .wiki.git
#                的写权限不可靠，必须使用具备 repo 范围的 PAT 或部署密钥。
#   REPO         可选，覆盖 <owner>/<repo>；默认从 git remote origin 推导。
#   WIKI_REPO    可选，覆盖完整的 wiki 仓库 clone URL（本地调试用）。
#   DOCS_DIR     可选，默认 <仓库根>/docs。
#   SYNC_BRANCH  可选，代码链接指向的分支，默认 main。
#
# 前置条件：目标仓库的 Wiki 必须已初始化（在网页端 Wiki 页签创建过任意一页），
# 否则 .wiki.git 仓库不存在，克隆会失败。
#
# 转换规则：
#   1. 页名 = docs/ 相对路径去 .md、/ → -（Home/_Sidebar/_Footer 保留原名）；
#   2. 剥离文件首部 YAML frontmatter（无 frontmatter 的文件直接跳过）；
#   3. 站内相对链接重写为扁平页名（锚点保留）；已写成的扁平页名原样通过；
#      行内代码段（反引号）与代码块内的示例不做重写；
#   4. 指向仓库代码文件的相对链接转 GitHub blob 绝对 URL；
#   5. 越界链接（指向 docs/ 之外且非仓库文件、或映射表外的 .md）报错退出。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DOCS_DIR="${DOCS_DIR:-$REPO_ROOT/docs}"
SYNC_BRANCH="${SYNC_BRANCH:-main}"

if [[ ! -d "$DOCS_DIR" ]]; then
    echo "错误：文档目录不存在：$DOCS_DIR" >&2
    exit 1
fi

# --- 推导 <owner>/<repo> ---
if [[ -z "${REPO:-}" ]]; then
    origin="$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null || true)"
    if [[ -z "$origin" ]]; then
        echo "错误：无法从 git remote origin 推导仓库名，请设置 REPO=<owner>/<repo>" >&2
        exit 1
    fi
    REPO="${origin#*github.com:}"
    REPO="${REPO#https://github.com/}"
    REPO="${REPO%.git}"
fi

# --- wiki 仓库 URL ---
if [[ -z "${WIKI_REPO:-}" ]]; then
    if [[ -z "${WIKI_TOKEN:-}" ]]; then
        echo "错误：需要 WIKI_TOKEN（具备 wiki 写权限的 PAT/部署密钥），" >&2
        echo "或直接提供 WIKI_REPO 完整 clone URL。" >&2
        exit 1
    fi
    WIKI_REPO="https://x-access-token:${WIKI_TOKEN}@github.com/${REPO}.wiki.git"
fi

SHA7="$(git -C "$REPO_ROOT" rev-parse --short=7 HEAD 2>/dev/null || echo unknown)"
GH_BASE="https://github.com/${REPO}"

WORK_DIR="$(mktemp -d)"
WIKI_DIR="$WORK_DIR/wiki"
OUT_DIR="$WORK_DIR/out"
trap 'rm -rf "$WORK_DIR"' EXIT

# --- 1. 克隆 wiki 仓库 ---
echo ">> 克隆 ${REPO}.wiki.git ..."
if ! git clone --quiet "$WIKI_REPO" "$WIKI_DIR" 2>"$WORK_DIR/clone.err"; then
    echo "错误：无法克隆 ${REPO}.wiki.git：" >&2
    sed 's/^/   /' "$WORK_DIR/clone.err" >&2
    echo "若该仓库 Wiki 从未启用（.wiki.git 不存在），请先在仓库 Wiki 页签手动创建任意一页以初始化。" >&2
    exit 1
fi

# --- 2. 转换 docs/ 为扁平页面 ---
echo ">> 转换 $DOCS_DIR ..."
DOCS_DIR="$DOCS_DIR" WIKI_OUT="$OUT_DIR" GH_BASE="$GH_BASE" \
SYNC_BRANCH="$SYNC_BRANCH" python3 - <<'PYEOF'
import os
import posixpath
import re
import sys

docs_dir = os.environ["DOCS_DIR"]
out_dir = os.environ["WIKI_OUT"]
gh_base = os.environ["GH_BASE"]
branch = os.environ["SYNC_BRANCH"]

SPECIAL = {"Home.md", "_Sidebar.md", "_Footer.md"}
LINK_RE = re.compile(r"(\]\()([^()\s]+)(\))")

# --- 构建页名映射（双射校验） ---
page_by_rel = {}  # docs/ 内相对路径 -> 页名（不含 .md）
for root, dirs, names in os.walk(docs_dir):
    dirs[:] = sorted(d for d in dirs if d != ".git")
    for name in sorted(names):
        if not name.endswith(".md"):
            continue
        rel = os.path.relpath(os.path.join(root, name), docs_dir).replace(os.sep, "/")
        if rel in SPECIAL:
            slug = rel[:-3]  # Home / _Sidebar / _Footer 保留原名
        else:
            slug = rel[:-3].replace("/", "-")
        if slug in page_by_rel.values():
            sys.exit(f"错误：页名冲突（映射非双射）：{rel} → {slug}")
        page_by_rel[rel] = slug

slug_set = set(page_by_rel.values())
errors = []


def rewrite_target(target, base_dir, rel):
    """重写单个链接目标；返回新目标，越界时记录错误。"""
    if target.startswith("#") or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("//"):
        return target  # 纯锚点 / 带 scheme 的绝对 URL

    path, anchor = target, ""
    if "#" in path:
        path, anchor = path.split("#", 1)
        anchor = "#" + anchor
    if not path:
        return target

    if path in slug_set:
        return target  # 已是扁平页名（如 _Sidebar.md/_Footer.md 内的链接），原样通过

    # 以 docs/ 为基准解析相对路径
    resolved = posixpath.normpath(posixpath.join("docs", base_dir, path))
    if resolved.startswith(".."):
        errors.append(f"{rel}: 越界链接（指向仓库之外）: {target}")
        return target

    if resolved.startswith("docs/") and resolved.endswith(".md"):
        inside = resolved[len("docs/"):]
        if inside not in page_by_rel:
            errors.append(f"{rel}: 站内链接目标不存在或不在映射表中: {target} → {resolved}")
            return target
        return page_by_rel[inside] + anchor

    # 仓库内代码/其他文件 → GitHub 绝对 URL
    return f"{gh_base}/blob/{branch}/{resolved}{anchor}"


def strip_frontmatter(lines):
    """剥离首部 YAML frontmatter；无 frontmatter 时原样返回。"""
    if not lines or lines[0].strip() != "---":
        return lines
    for i in range(1, min(len(lines), 60)):
        if lines[i].strip() in ("---", "..."):
            rest = lines[i + 1:]
            while rest and not rest[0].strip():
                rest = rest[1:]
            return rest
    return lines  # 未找到闭合分隔线，视为普通内容


def rewrite_outside_inline_code(line, base_dir, rel):
    """只重写行内代码段之外的链接；反引号包裹的示例/代码原样保留。"""
    parts = line.split("`")
    for i in range(0, len(parts), 2):
        parts[i] = LINK_RE.sub(
            lambda m: m.group(1) + rewrite_target(m.group(2), base_dir, rel) + m.group(3),
            parts[i],
        )
    return "`".join(parts)


def convert(rel):
    with open(os.path.join(docs_dir, rel), encoding="utf-8") as f:
        lines = strip_frontmatter(f.read().split("\n"))

    base_dir = posixpath.dirname(rel)
    out_lines = []
    in_fence = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if in_fence:
            out_lines.append(line)
            continue
        out_lines.append(rewrite_outside_inline_code(line, base_dir, rel))

    target = os.path.join(out_dir, page_by_rel[rel] + ".md")
    os.makedirs(out_dir, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines))
    print(f"   {rel} -> {page_by_rel[rel]}.md")


for rel in page_by_rel:
    convert(rel)

if errors:
    sys.exit("错误：链接重写失败：\n  " + "\n  ".join(errors))

print(f">> 共 {len(page_by_rel)} 个页面，映射双射校验通过。")
PYEOF

# --- 3. 清空 wiki 工作区（保留 .git）并写入新内容 ---
echo ">> 清空 wiki 工作区 ..."
find "$WIKI_DIR" -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} +
cp -R "$OUT_DIR/." "$WIKI_DIR/"

# --- 4. 提交并推送（无变化则跳过） ---
git -C "$WIKI_DIR" add -A
if git -C "$WIKI_DIR" diff --cached --quiet; then
    echo ">> wiki 无变化，跳过推送。"
    exit 0
fi

DEFAULT_BRANCH="$(git -C "$WIKI_DIR" symbolic-ref --short HEAD 2>/dev/null || echo master)"
git -C "$WIKI_DIR" \
    -c user.name="instantboard-wiki-sync" \
    -c user.email="instantboard-wiki-sync@users.noreply.github.com" \
    commit --quiet -m "docs: sync wiki from ${SYNC_BRANCH}@${SHA7}"
git -C "$WIKI_DIR" push --quiet origin "HEAD:${DEFAULT_BRANCH}"
echo ">> 已推送至 ${REPO}.wiki.git（${DEFAULT_BRANCH}）：docs: sync wiki from ${SYNC_BRANCH}@${SHA7}"
