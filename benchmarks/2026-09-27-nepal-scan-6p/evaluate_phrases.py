#!/usr/bin/env python3
"""Check exact recovery of selected phrases after whitespace/NFKC normalization."""

from pathlib import Path
import unicodedata


ROOT = Path(__file__).resolve().parent
TITLE = "尼共毛（汪亭友）测试"
TARGETS = [
    "研究目的、研究现状及遵循的原则和方法",
    "尼泊尔共产党（毛泽东主义者）",
    "农村包围城市武装夺取全国政权",
    "7个主力师的3万多正规军",
    "识形态、两条发展道路、两个政权、两支军队",
    "课题的研究价值和现实意义",
    "我国维护西部边陲安全和稳定",
    "历来受到印度以及美、英等西方大国垂涎",
    "美国借帮助尼政府清剿尼共（毛）之机",
    "美、英等西方国家如此觊觎一个远在南亚的小国",
    "Siddharth Varadarajan",
    "Multiparty Democracy in Nepal will Be Message to Indian Naxalites",
]


def normalize(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def marker_output() -> Path:
    matches = sorted((ROOT / "marker2" / "raw").rglob("*.md"))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one Marker Markdown file, found: {matches}")
    return matches[0]


def main() -> None:
    files = {
        "PP-StructureV3": ROOT / "pp-structurev3" / f"{TITLE}.paginated.md",
        "Docling": ROOT / "docling" / f"{TITLE}.paginated.md",
        "Marker 2": marker_output(),
        "OCRmyPDF": ROOT / "ocrmypdf" / f"{TITLE}.paginated.md",
    }
    normalized = {
        name: normalize(path.read_text(encoding="utf-8")) for name, path in files.items()
    }
    lines = [
        "# 关键短语机械核对",
        "",
        "规则：只做 Unicode NFKC 与空白删除；不做错别字、标点或括号纠正。",
        "这不是完整字符错误率，只用于横向抽样。",
        "",
        "| # | 目标短语 | PP-StructureV3 | Docling | Marker 2 | OCRmyPDF |",
        "|---:|---|:---:|:---:|:---:|:---:|",
    ]
    scores = {name: 0 for name in files}
    for index, target in enumerate(TARGETS, 1):
        cells = []
        needle = normalize(target)
        for name in files:
            hit = needle in normalized[name]
            scores[name] += int(hit)
            cells.append("✓" if hit else "✗")
        lines.append(f"| {index} | {target} | " + " | ".join(cells) + " |")
    lines.extend(
        [
            "",
            "| 工具 | 命中数 |",
            "|---|---:|",
            *[f"| {name} | {score}/{len(TARGETS)} |" for name, score in scores.items()],
            "",
        ]
    )
    output = ROOT / "关键短语机械核对.md"
    output.write_text("\n".join(lines), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
