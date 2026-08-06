from __future__ import annotations

import json
from pathlib import Path
import sys

from app.paths import user_data_dir


WD_FORMAT_DOCUMENT_DEFAULT = 16
MSO_AUTOMATION_SECURITY_FORCE_DISABLE = 3


def _prepare_win32com_cache() -> None:
    import win32com

    cache_dir = user_data_dir() / "recruiting" / "win32com-gen-py"
    cache_dir.mkdir(parents=True, exist_ok=True)
    win32com.__gen_path__ = str(cache_dir)


def probe() -> dict[str, object]:
    try:
        _prepare_win32com_cache()
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        word = win32com.client.DispatchEx("Word.Application")
        try:
            version = str(word.Version)
        finally:
            word.Quit()
            pythoncom.CoUninitialize()
        return {"available": True, "version": version, "error_code": None}
    except Exception:
        return {"available": False, "version": None, "error_code": "DOCUMENT_CONVERTER_UNAVAILABLE"}


def convert(source: Path, output: Path) -> None:
    _prepare_win32com_cache()
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    word = win32com.client.DispatchEx("Word.Application")
    document = None
    try:
        word.Visible = False
        word.DisplayAlerts = 0
        word.AutomationSecurity = MSO_AUTOMATION_SECURITY_FORCE_DISABLE
        document = word.Documents.Open(
            str(source.resolve()),
            ConfirmConversions=False,
            ReadOnly=True,
            AddToRecentFiles=False,
            Revert=False,
            NoEncodingDialog=True,
            OpenAndRepair=False,
        )
        document.SaveAs2(str(output.resolve()), FileFormat=WD_FORMAT_DOCUMENT_DEFAULT, AddToRecentFiles=False)
    finally:
        if document is not None:
            document.Close(SaveChanges=False)
        word.Quit()
        pythoncom.CoUninitialize()


def main(argv: list[str]) -> int:
    if argv == ["--probe"]:
        result = probe()
        print(json.dumps(result))
        return 0 if result["available"] else 2
    if len(argv) != 2:
        return 2
    try:
        convert(Path(argv[0]), Path(argv[1]))
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
