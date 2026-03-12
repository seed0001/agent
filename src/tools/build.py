"""Build orchestrator for web and Python projects."""
import asyncio
import shutil
from pathlib import Path


async def run_build(project_path: str, project_type: str = "auto") -> str:
    """
    Run build for a project. project_type: auto, web, python
    """
    p = Path(project_path).expanduser()
    if not p.exists() or not p.is_dir():
        return f"Error: Project path not found: {project_path}"

    if project_type == "auto":
        if (p / "package.json").exists():
            project_type = "web"
        elif (p / "pyproject.toml").exists() or (p / "setup.py").exists() or (p / "requirements.txt").exists():
            project_type = "python"
        else:
            return "Could not detect project type. Specify 'web' or 'python'."

    try:
        if project_type == "web":
            return await _build_web(p)
        elif project_type == "python":
            return await _build_python(p)
        return f"Unknown project type: {project_type}"
    except Exception as e:
        return f"Build error: {e}"


async def _build_web(p: Path) -> str:
    """npm/yarn/pnpm build."""
    for runner in ["npm", "yarn", "pnpm"]:
        if shutil.which(runner):
            proc = await asyncio.create_subprocess_shell(
                f"{runner} run build",
                cwd=str(p),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await proc.communicate()
            text = out.decode("utf-8", errors="replace")
            return f"Build exit {proc.returncode}\n{text}"
    return "No npm/yarn/pnpm found"


async def _build_python(p: Path) -> str:
    """Python build (pip install, etc)."""
    proc = await asyncio.create_subprocess_shell(
        "pip install -e . 2>&1 || pip install -r requirements.txt 2>&1",
        cwd=str(p),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return f"Install exit {proc.returncode}\n{out.decode('utf-8', errors='replace')}"
