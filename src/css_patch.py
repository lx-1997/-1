import re

FILE = "/Users/d-robotics/Desktop/超级智能体/-1-main/src/index.css"

with open(FILE, "r", encoding="utf-8") as f:
    content = f.read()

original = content
stats = []
total_replaced = 0

def record(label, count):
    stats.append((label, count))
    return count

# ============================================================
# 1. TOUCH TARGETS: interactive elements < 44px → at least 44px
# ============================================================

# 1a. .market-pill (first occurrence with height: 30px in the block)
old = ".market-pill {\n  display: inline-flex;\n  align-items: center;\n  gap: 4px;\n  height: 30px;\n  padding: 0 9px;\n  border: 1px solid var(--border-soft);\n  border-radius: 5px;\n  background: var(--surface-muted);\n  font-size: 12px;\n  color: var(--text-muted);"
new = ".market-pill {\n  display: inline-flex;\n  align-items: center;\n  gap: 4px;\n  min-height: 44px;\n  padding: 0 9px;\n  border: 1px solid var(--border-soft);\n  border-radius: 5px;\n  background: var(--surface-muted);\n  font-size: 12px;\n  color: var(--text-muted);"
if old in content:
    content = content.replace(old, new, 1)
    total_replaced += record("touch: .market-pill height:30px → min-height:44px", 1)
else:
    record("touch: .market-pill (NOT FOUND)", 0)

# 1b. .workspace-sidebar .ant-menu-item height:34px → min-height:44px
old = ".workspace-sidebar .ant-menu-item {\n  margin: 3px 0;\n  height: 34px;\n  line-height: 34px;"
new = ".workspace-sidebar .ant-menu-item {\n  margin: 3px 0;\n  min-height: 44px;\n  line-height: 34px;"
if old in content:
    content = content.replace(old, new, 1)
    total_replaced += record("touch: .ant-menu-item height:34px → min-height:44px", 1)
else:
    record("touch: .ant-menu-item (NOT FOUND)", 0)

# 1c. .research-workbench-bridge textarea min-height:34px !important → 44px
old = ".research-workbench-bridge textarea.ant-input {\n  min-height: 34px !important;"
new = ".research-workbench-bridge textarea.ant-input {\n  min-height: 44px !important;"
if old in content:
    content = content.replace(old, new, 1)
    total_replaced += record("touch: textarea min-height:34px → 44px !important", 1)
else:
    record("touch: textarea min-height:34px (NOT FOUND)", 0)

# 1d. .investor-chat-home .chatgpt-suggestions button min-height:38px → 44px
old = ".investor-chat-home .chatgpt-suggestions button {\n  min-height: 38px;\n  padding: 8px 14px;"
new = ".investor-chat-home .chatgpt-suggestions button {\n  min-height: 44px;\n  padding: 8px 14px;"
if old in content:
    content = content.replace(old, new, 1)
    total_replaced += record("touch: .chatgpt-suggestions button min-height:38px → 44px", 1)
else:
    record("touch: .chatgpt-suggestions button (NOT FOUND)", 0)

# 1e. .chatgpt-capability-pills .ant-btn height:38px → min-height:44px
old = ".chatgpt-capability-pills .ant-btn {\n  height: 38px;\n  border-radius: 999px !important;"
new = ".chatgpt-capability-pills .ant-btn {\n  min-height: 44px;\n  border-radius: 999px !important;"
if old in content:
    content = content.replace(old, new, 1)
    total_replaced += record("touch: .chatgpt-capability-pills .ant-btn height:38px → min-height:44px", 1)
else:
    record("touch: .chatgpt-capability-pills .ant-btn (NOT FOUND)", 0)

# 1f. .chatgpt-message-toolbar .ant-btn width/height 28px → 44px
old = ".chatgpt-message-toolbar .ant-btn {\n  width: 28px;\n  height: 28px;\n  color: #6b6f76;"
new = ".chatgpt-message-toolbar .ant-btn {\n  width: 44px;\n  height: 44px;\n  color: #6b6f76;"
if old in content:
    content = content.replace(old, new, 1)
    total_replaced += record("touch: .chatgpt-message-toolbar .ant-btn 28x28 → 44x44", 1)
else:
    record("touch: .chatgpt-message-toolbar .ant-btn (NOT FOUND)", 0)

# 1g. min-height:42px → 44px (the one at L1112 - context: gap:8px;align-items:center;min-height:42px;padding:8px 10px;border:1px solid var(--border-soft))
old = "  gap: 8px;\n  align-items: center;\n  min-height: 42px;\n  padding: 8px 10px;\n  border: 1px solid var(--border-soft);\n  border-radius: 6px;"
new = "  gap: 8px;\n  align-items: center;\n  min-height: 44px;\n  padding: 8px 10px;\n  border: 1px solid var(--border-soft);\n  border-radius: 6px;"
if old in content:
    content = content.replace(old, new, 1)
    total_replaced += record("touch: min-height:42px → 44px (L1112 context)", 1)
else:
    record("touch: min-height:42px (NOT FOUND)", 0)


# ============================================================
# 2. 375px NARROW SCREEN FIXES
# ============================================================

# 2a. .ai-capacity-table-wrap .ant-table min-width:760px → min-width:unset;overflow-x:auto
old = ".ai-capacity-table-wrap .ant-table {\n  min-width: 760px;\n}"
new = ".ai-capacity-table-wrap .ant-table {\n  min-width: unset;\n  overflow-x: auto;\n}"
if old in content:
    content = content.replace(old, new, 1)
    total_replaced += record("narrow: min-width:760px → unset+overflow-x:auto", 1)
else:
    record("narrow: min-width:760px (NOT FOUND)", 0)

# 2b. Add table container overflow protection inside @media (max-width:480px) block
old_media_block = """  .product-price-row {
    align-items: flex-start;
    flex-direction: column;
  }
}"""
new_media_block = """  .product-price-row {
    align-items: flex-start;
    flex-direction: column;
  }

  .ant-table-wrapper,
  .ant-table-container {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }
}"""
if old_media_block in content:
    content = content.replace(old_media_block, new_media_block, 1)
    total_replaced += record("narrow: added table overflow protection in @media(max-width:480px)", 1)
else:
    record("narrow: @media(480px) block ending (NOT FOUND)", 0)


# ============================================================
# 3. COLOR CONTRAST FIXES
# ============================================================

color_map = {
    "#748792": "#5f6b75",
    "#96a7b2": "#8a9ba8",
    "#93a4af": "#8798a5",
    "#5f6368": "#4a4f54",
    "#777b82": "#5a5e65",
    "#676b72": "#50545a",
}

for old_color, new_color in color_map.items():
    count = content.count(old_color)
    if count > 0:
        content = content.replace(old_color, new_color)
        total_replaced += record(f"color: {old_color} → {new_color}", count)
    else:
        record(f"color: {old_color} (NOT FOUND)", 0)


# ============================================================
# 4. CSS VARIABLES: radius → use var() (only after line 500)
# ============================================================

lines = content.split("\n")
radius_changes = 0

for i in range(500, len(lines)):
    line = lines[i]
    # border-radius: 6px; → var(--radius-sm)
    # but NOT if it already uses var()
    if "border-radius: 6px" in line and "var(" not in line:
        lines[i] = line.replace("border-radius: 6px", "border-radius: var(--radius-sm)")
        radius_changes += 1
    elif "border-radius: 10px" in line and "var(" not in line:
        lines[i] = line.replace("border-radius: 10px", "border-radius: var(--radius-md)")
        radius_changes += 1
    elif "border-radius: 14px" in line and "var(" not in line:
        lines[i] = line.replace("border-radius: 14px", "border-radius: var(--radius-lg)")
        radius_changes += 1

content = "\n".join(lines)
total_replaced += record("radius: border-radius:Xpx → var(--radius-*) (after L500)", radius_changes)


# ============================================================
# 5. SKELETON SCREEN STYLES (append to end)
# ============================================================

skeleton_css = """
.skeleton-wave {
  background: linear-gradient(90deg,var(--surface-muted) 25%,#e8ecf0 37%,var(--surface-muted) 63%);
  background-size:200% 100%;
  animation:skeleton-loading 1.4s ease infinite;
  border-radius:var(--radius-sm);
}
@keyframes skeleton-loading{0%{background-position:200% 0}100%{background-position:-200% 0}}
.skeleton-line{height:14px;margin-bottom:10px}
.skeleton-line.short{width:60%}
.skeleton-line.medium{width:80%}
.skeleton-block{height:80px;margin-bottom:16px}
.skeleton-avatar{width:44px;height:44px;border-radius:50%}
"""

content += skeleton_css
total_replaced += record("skeleton: added .skeleton-wave / .skeleton-line / .skeleton-block / .skeleton-avatar", 1)


# ============================================================
# 6. FOCUS-VISIBLE STYLES (append to end)
# ============================================================

focus_css = """
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.ant-btn:focus-visible{outline-offset:1px}
"""

content += focus_css
total_replaced += record("focus: added :focus-visible global styles", 1)


# ============================================================
# WRITE BACK
# ============================================================

with open(FILE, "w", encoding="utf-8") as f:
    f.write(content)

# ============================================================
# REPORT
# ============================================================

print("=" * 60)
print("CSS 优化修改统计报告")
print("=" * 60)
for label, count in stats:
    status = f"✓ {count}处" if count > 0 else "✗ 未找到（跳过）"
    print(f"  [{status}] {label}")
print("=" * 60)
print(f"总计修改: {total_replaced} 处")
print("=" * 60)
