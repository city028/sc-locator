# PyInstaller build for sc-locator.exe, with Tesseract packed inside.
#   pip install pyinstaller
#   pyinstaller sc_locator.spec
# Needs Tesseract installed locally (winget install UB-Mannheim.TesseractOCR).
# Set TESSERACT_DIR if it is not in the default place.
import os
import pefile

TESS = os.environ.get("TESSERACT_DIR", r"C:\Program Files\Tesseract-OCR")


def needed(root, start="tesseract.exe"):
    """tesseract.exe and the DLLs from its folder it loads, nothing else.

    The install is ~240 MB, mostly training tools that are never run.
    """
    local = {f.lower(): f for f in os.listdir(root)}
    seen, todo = set(), [start]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        pe = pefile.PE(os.path.join(root, local[name]), fast_load=True)
        pe.parse_data_directories(directories=[
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
        for attr in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
            for entry in getattr(pe, attr, []):
                dll = entry.dll.decode().lower()
                if dll in local:
                    todo.append(dll)
    return [os.path.join(root, local[n]) for n in sorted(seen)]


binaries = [(path, "tesseract") for path in needed(TESS)]
datas = [
    (os.path.join(TESS, "tessdata", "eng.traineddata"), "tesseract/tessdata"),
    (os.path.join(TESS, "doc", "LICENSE"), "tesseract"),
]

a = Analysis(["sc_locator.py"], binaries=binaries, datas=datas)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, name="sc-locator",
          console=True, upx=False)
