#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
UVR5 人声伴奏分离 & 去混响去延迟 — 交互式菜单
自动扫描 workspace\项目\export\人物\wavs 目录
"""

import os
import sys
import subprocess
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
UVR5_DIR = ROOT / "0a-UVR5"
VENV_PYTHON = ROOT / "dependencies" / "ecapa" / ".venv" / "Scripts" / "python.exe"
OUTPUT = UVR5_DIR / "output"
CLI_PY = UVR5_DIR / "cli.py"

MODELS = {
    "separate": {
        "label": "人声伴奏分离 (BS-Roformer)",
        "model": "model_bs_roformer_ep_317_sdr_12.9755",
        "prefix": "separate",
    },
    "dereverb": {
        "label": "去混响去延迟 (VR-DeEchoAggressive)",
        "model": "VR-DeEchoAggressive",
        "prefix": "dereverb",
    },
}


def scan_targets() -> list[Path]:
    targets = []
    ws = ROOT / "workspace"
    if not ws.is_dir():
        return targets
    for proj in sorted(ws.iterdir()):
        if not proj.is_dir():
            continue
        export_dir = proj / "export"
        if not export_dir.is_dir():
            continue
        for char in sorted(export_dir.iterdir()):
            if not char.is_dir():
                continue
            wavs = char / "wavs"
            if wavs.is_dir() and list(wavs.glob("*.wav")):
                targets.append(wavs)
    return targets


def check_prereqs() -> bool:
    ok = True
    if not VENV_PYTHON.exists():
        print(f"[错误] 找不到 Python: {VENV_PYTHON}")
        print("请先运行 run.bat 初始化环境")
        ok = False
    if not CLI_PY.exists():
        print(f"[错误] 找不到 UVR5 CLI: {CLI_PY}")
        ok = False
    return ok


def pick_target(targets: list[Path], mode: str) -> Path | None:
    while True:
        os.system("cls")
        print("=" * 40)
        print(f"  {MODELS[mode]['label']}")
        print("=" * 40)
        print()
        print("  选择要处理的 wavs 目录:")
        for i, t in enumerate(targets, 1):
            print(f"    [{i}] {t}")
        print()
        print("  [B] 返回")
        print()
        choice = input("请输入编号: ").strip()
        if choice.upper() == "B":
            return None
        try:
            idx = int(choice)
            if 1 <= idx <= len(targets):
                return targets[idx - 1]
        except ValueError:
            pass
        print("无效编号")
        input("按任意键继续...")


def run_model(input_dir: Path, model_name: str, output_dir: Path, keep_inst_flag: str = ""):
    if output_dir.exists():
        print(f"[警告] 输出目录已存在，删除中: {output_dir}")
        import shutil
        shutil.rmtree(output_dir, ignore_errors=True)

    cmd = [
        str(VENV_PYTHON), str(CLI_PY),
        "--input", str(input_dir),
        "--output", str(output_dir),
        "--model", model_name,
        "--format", "wav",
    ]
    if keep_inst_flag:
        cmd.append(keep_inst_flag)

    print()
    print("=" * 40)
    print(f"  模型 : {model_name}")
    print(f"  输入 : {input_dir}")
    print(f"  输出 : {output_dir}")
    if keep_inst_flag:
        print(f"  伴奏 : 是")
    print("=" * 40)
    print()

    subprocess.run(cmd, cwd=str(UVR5_DIR))


def main():
    if not check_prereqs():
        input("按任意键退出...")
        sys.exit(1)

    targets = scan_targets()
    if not targets:
        print("[错误] 未找到 wavs 目录 (workspace\\*\\export\\*\\wavs)")
        print("请先通过前端导出音频")
        input("按任意键退出...")
        sys.exit(1)

    while True:
        os.system("cls")
        print("=" * 40)
        print("  UVR5 人声伴奏分离 & 去混响去延迟工具")
        print("=" * 40)
        print()
        for key, info in MODELS.items():
            print(f"  [{list(MODELS).index(key) + 1}] {info['label']}")
        print("  [Q] 退出")
        print()
        print("  目标目录:")
        for i, t in enumerate(targets, 1):
            print(f"    [{i}] {t}")
        print()
        choice = input("请选择操作: ").strip()
        if choice.upper() == "Q":
            break

        mode = None
        if choice == "1":
            mode = "separate"
        elif choice == "2":
            mode = "dereverb"

        if mode is None:
            print("无效选择")
            input("按任意键继续...")
            continue

        target = pick_target(targets, mode)
        if target is None:
            continue

        print()
        keep_inst = input("  是否同时输出 instrument/伴奏? [y/N]: ").strip().lower()
        keep_inst_flag = "--keep-inst" if keep_inst == "y" else ""

        info = MODELS[mode]
        output_dir = OUTPUT / f"{info['prefix']}_{target.name}"

        run_model(target, info["model"], output_dir, keep_inst_flag)

        print()
        print("=" * 40)
        print(f"  完成! 结果: {output_dir}")
        print("=" * 40)
        input("按任意键继续...")

    sys.exit(0)


if __name__ == "__main__":
    main()
