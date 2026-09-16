from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from huggingface_hub import snapshot_download


MODEL_ID = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
MODEL_FILES = (
    "1_Pooling/config.json",
    "config.json",
    "model.safetensors",
    "modules.json",
    "sentence_bert_config.json",
    "sentencepiece.bpe.model",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
)


def _is_complete(destination: Path) -> bool:
    revision_file = destination / ".revision"
    return (
        revision_file.is_file()
        and revision_file.read_text().strip() == MODEL_REVISION
        and all((destination / name).is_file() for name in MODEL_FILES)
    )


def install_model(destination: Path) -> None:
    if _is_complete(destination):
        print(f"E5 model already available at {destination}")
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="e5-download-") as temporary_directory:
        source = Path(
            snapshot_download(
                repo_id=MODEL_ID,
                revision=MODEL_REVISION,
                local_dir=temporary_directory,
                allow_patterns=list(MODEL_FILES),
            )
        )
        missing = [name for name in MODEL_FILES if not (source / name).is_file()]
        if missing:
            raise RuntimeError(f"E5 model is missing files: {', '.join(missing)}")

        staging = destination.with_name(f"{destination.name}.staging")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        for name in MODEL_FILES:
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, target, follow_symlinks=True)
        (staging / ".revision").write_text(f"{MODEL_REVISION}\n")
        if destination.exists():
            shutil.rmtree(destination)
        staging.rename(destination)

    print(f"E5 model {MODEL_REVISION} ready at {destination}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download the pinned E5 model files")
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    install_model(args.destination)


if __name__ == "__main__":
    main()
