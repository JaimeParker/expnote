"""Document roots and paths that survive a vault rebind."""

from pathlib import Path

from expnote.research.errors import ResearchError
from expnote.research.newnote import DEFAULT_FOLDERS, folders_for

KINDS = ("ev", "res")
LOCATIONS = ("vault", "research")


def document_dir(resolved, profile, kind: str, location: str | None = None) -> Path:
    folders = folders_for(profile) if profile else DEFAULT_FOLDERS
    split = resolved.research_dir is not None
    vault_owned = kind == "plan" or (
        kind in {"ev", "appendix", "res"}
        and (location or resolved.entry.storage(kind)) == "vault"
    )
    root = resolved.project_dir if vault_owned else resolved.material_dir
    if kind == "appendix" and vault_owned and split:
        path = root / folders["ev"] / "_appendices"
    else:
        path = root / folders[kind]
    if not path.resolve().is_relative_to(root.resolve()):
        raise ResearchError("STORAGE_PATH", f"{path} is outside {root}", exit_code=5)
    return path


def vault_assets_dir(resolved) -> Path:
    return resolved.project_dir / "assets"


def location_data(resolved, profile) -> dict:
    return {
        "layout": resolved.entry.layout,
        "evidenceLocation": resolved.entry.storage("ev"),
        "resultsLocation": resolved.entry.storage("res"),
        "resultsInVault": resolved.entry.results_in_vault,
        "absImagesDir": str(resolved.images_dir),
        "absEvidenceDir": str(document_dir(resolved, profile, "ev")),
        "absEvidenceAppendixDir": str(document_dir(resolved, profile, "appendix")),
        "absResultsDir": str(document_dir(resolved, profile, "res")),
        "absVaultImagesDir": str(vault_assets_dir(resolved)),
    }


def file_key(resolved, path: Path) -> str:
    if path.is_relative_to(resolved.material_dir):
        return path.relative_to(resolved.material_dir).as_posix()
    return "@vault/" + path.relative_to(resolved.vault_root).as_posix()


def file_path(resolved, key: str) -> Path:
    vault = key.startswith("@vault/")
    root = resolved.vault_root if vault else resolved.material_dir
    path = root / (key.removeprefix("@vault/") if vault else key)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ResearchError("STORAGE_PATH", f"{path} is outside {root}", exit_code=5)
    return path
