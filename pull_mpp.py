# -*- coding: utf-8 -*-
"""
SVN .mpp (Microsoft Project) 文件独立拉取与管理小工具
功能：
1. 运行时单独拉取指定的 .mpp 文件到本地目录并支持直接用 Microsoft Project 打开。
2. 支持输入完整 SVN URL、仓库相对路径、或关键词模糊匹配拉取。
3. 提供简洁的交互式命令行界面与可在其他模块中直接调用的 API 函数。
"""

import sys
import os
import subprocess
import shutil
import argparse
import urllib.parse
from datetime import datetime

DEFAULT_SVN_REPO = "https://dell740-svn/svn/ElectronicCentreRepository/Electronic trunk"


def canonicalize_svn_url(url: str) -> str:
    """标准转义 SVN URL 中的特殊字符（安全保留斜杠与常见字符）"""
    if not url or not url.startswith(("http://", "https://", "svn://")):
        return url
    url_unquoted = urllib.parse.unquote(url)
    parsed = urllib.parse.urlsplit(url_unquoted)
    quoted_path = urllib.parse.quote(parsed.path, safe="/")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, quoted_path, parsed.query, parsed.fragment))


def find_svn_exe() -> str:
    """定位 svn.exe 命令行工具路径"""
    p = shutil.which("svn.exe") or shutil.which("svn")
    if p and os.path.exists(p):
        return p
    candidates = [
        r"C:\Program Files\TortoiseSVN\bin\svn.exe",
        r"C:\Program Files (x86)\TortoiseSVN\bin\svn.exe",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return "svn"


def build_full_url(base_repo: str, rel_path: str) -> str:
    """拼接完整 SVN URL"""
    rel = rel_path.strip().replace("\\", "/")
    if rel.startswith(("http://", "https://", "svn://")):
        return rel
    base = base_repo.rstrip("/") + "/"
    return base + rel.lstrip("/")


def pull_mpp_file(file_url_or_path: str, save_path: str = None, open_after: bool = False,
                  base_repo: str = DEFAULT_SVN_REPO) -> tuple:
    """
    单独拉取指定的 .mpp 文件到本地。
    参数:
        file_url_or_path: .mpp 文件的完整 SVN URL 或相对仓库的相对路径
        save_path: 本地保存的目标路径（文件路径或目录路径），默认保存到当前用户 Downloads 目录
        open_after: 拉取完成后是否自动调用系统关联程序（如 Project）打开该文件
        base_repo: 若传入的是相对路径，则以此作为基础仓库 URL
    返回:
        (bool_success, final_save_path, message)
    """
    if not file_url_or_path:
        return False, "", "请指定要拉取的 .mpp 文件路径或 URL"

    full_url = build_full_url(base_repo, file_url_or_path)
    clean_url = canonicalize_svn_url(full_url)
    file_name = full_url.rstrip("/").split("/")[-1]

    if not file_name.lower().endswith(".mpp"):
        file_name += ".mpp"

    if not save_path:
        downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        os.makedirs(downloads_dir, exist_ok=True)
        save_path = os.path.join(downloads_dir, file_name)
    elif os.path.isdir(save_path):
        save_path = os.path.join(save_path, file_name)

    os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)

    svn_exe = find_svn_exe()
    cmd = [svn_exe, "export", clean_url, save_path, "--force", "--non-interactive"]

    kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        p = subprocess.Popen(cmd, **kwargs)
        out_b, err_b = p.communicate(timeout=60)
        if p.returncode == 0:
            msg = f"拉取成功！文件已保存至: {save_path}"
            if open_after and os.path.exists(save_path):
                try:
                    os.startfile(save_path)
                    msg += " (已调用系统默认关联程序打开)"
                except Exception as oe:
                    msg += f" (打开文件失败: {oe})"
            return True, save_path, msg
        else:
            err_msg = ""
            for enc in ("gbk", "utf-8", "cp936"):
                try:
                    err_msg = err_b.decode(enc)
                    break
                except Exception:
                    pass
            return False, save_path, f"SVN 导出失败 (代码 {p.returncode}):\n{err_msg.strip()}"
    except subprocess.TimeoutExpired:
        return False, save_path, "拉取超时（超过 60 秒），请检查网络连接。"
    except Exception as e:
        return False, save_path, f"执行异常: {str(e)}"


def list_cached_mpp_files() -> list:
    """从本地缓存目录中快速提取所有已收录的 .mpp 文件列表"""
    cache_dir = os.path.join(os.path.expanduser("~"), ".svn_search_cache")
    results = []
    if not os.path.exists(cache_dir):
        return results

    for fname in os.listdir(cache_dir):
        if fname.startswith("cache_") and fname.endswith(".txt"):
            p = os.path.join(cache_dir, fname)
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if line.lower().endswith(".mpp"):
                            if line not in results:
                                results.append(line)
            except Exception:
                pass
    return results


def interactive_cli():
    """交互式控制台拉取"""
    print("=" * 60)
    print("⚡ SVN 单独拉取 .mpp 项目计划文件实用工具")
    print("=" * 60)
    print(f"当前默认基础仓库: {DEFAULT_SVN_REPO}")

    cached_mpps = list_cached_mpp_files()
    if cached_mpps:
        print(f"\n[本地缓存中已发现 {len(cached_mpps)} 个 .mpp 文件记录]:")
        for i, m in enumerate(cached_mpps[:8], 1):
            print(f"  {i}. {m}")
        if len(cached_mpps) > 8:
            print(f"  ... 更多共 {len(cached_mpps)} 项")

    print("\n请选择操作:")
    print("1. 输入编号直接拉取上述缓存文件")
    print("2. 输入或粘贴完整的 SVN URL 或相对路径单独拉取")
    print("3. 退出")

    choice = input("\n请输入选项 (1/2/3): ").strip()
    if choice == "1" and cached_mpps:
        idx_str = input(f"请输入要拉取的序号 (1-{len(cached_mpps)}): ").strip()
        try:
            idx = int(idx_str) - 1
            if 0 <= idx < len(cached_mpps):
                target_file = cached_mpps[idx]
                print(f"\n正在拉取: {target_file} ...")
                ok, path, msg = pull_mpp_file(target_file, open_after=True)
                print(msg)
            else:
                print("无效序号。")
        except ValueError:
            print("输入有误。")
    elif choice == "2":
        path_input = input("请输入 .mpp 文件的相对路径或完整 URL: ").strip()
        if path_input:
            print(f"\n正在拉取: {path_input} ...")
            ok, path, msg = pull_mpp_file(path_input, open_after=True)
            print(msg)
    else:
        print("已退出。")


def main():
    parser = argparse.ArgumentParser(description="SVN .mpp 文件独立拉取工具")
    parser.add_argument("--url", "-u", help="要拉取的 .mpp 文件的 URL 或相对路径")
    parser.add_argument("--out", "-o", help="本地保存路径")
    parser.add_argument("--open", action="store_true", help="拉取完成后自动打开文件")
    parser.add_argument("--list", "-l", action="store_true", help="列出本地索引中已收录的 .mpp 文件")

    args = parser.parse_args()

    if args.list:
        mpps = list_cached_mpp_files()
        print(f"本地缓存发现 {len(mpps)} 个 .mpp 文件:")
        for m in mpps:
            print(" ", m)
        return

    if args.url:
        ok, path, msg = pull_mpp_file(args.url, save_path=args.out, open_after=args.open)
        print(msg)
        sys.exit(0 if ok else 1)

    interactive_cli()


if __name__ == "__main__":
    main()

