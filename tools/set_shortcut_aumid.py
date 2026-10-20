"""Set System.AppUserModel.ID on TOPIK Study Hub shortcuts.

Ensures taskbar and desktop shortcuts match the AUMID set in main.py
("topik.study.hub"), preventing Windows from spawning duplicate taskbar icons.
"""

import ctypes
import os
import sys

try:
    from win32com.propsys import propsys, pscon
except ImportError:
    print("win32com is required to set shortcut AUMID.")
    sys.exit(1)

AUMID = "topik.study.hub"


def set_shortcut_aumid(lnk_path: str, aumid: str = AUMID) -> bool:
    if not os.path.exists(lnk_path):
        return False
    abs_path = os.path.abspath(lnk_path)
    store = propsys.SHGetPropertyStoreFromParsingName(
        abs_path, None, 2, propsys.IID_IPropertyStore
    )
    store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(aumid))
    store.Commit()
    del store
    return True


def main():
    taskbar_lnk = os.path.expandvars(
        r"%APPDATA%\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\TOPIK Study Hub.lnk"
    )
    desktop_lnk = os.path.expandvars(r"%USERPROFILE%\Desktop\TOPIK Study Hub.lnk")

    updated = []
    for path in [taskbar_lnk, desktop_lnk]:
        if set_shortcut_aumid(path):
            updated.append(path)

    if updated:
        # SHCNE_ASSOCCHANGED = 0x08000000
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
        print(f"Updated AUMID '{AUMID}' on {len(updated)} shortcut(s):")
        for p in updated:
            print(f"  - {p}")
    else:
        print("No matching shortcuts found.")


if __name__ == "__main__":
    main()
