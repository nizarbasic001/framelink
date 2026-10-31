[README-framelink.md](https://github.com/user-attachments/files/34216731/README-framelink.md)
# FrameLink for Windows

FrameLink starts with a 15-day free trial on first launch. Trial state is saved locally. After the trial ends, the app opens a dedicated dark activation window; valid signed offline license codes show a success page and then return to the existing main app. The activation page's purchase URL and support email are intentionally unconfigured: set `BUY_LICENSE_URL` and `SUPPORT_EMAIL` in `framelink.py` before distributing a public build. FrameLink uses one main window for the dashboard and setup pages. **Connect to OBS** is blue and **Mobile screen preview** is orange. The command console stays hidden during scrcpy sessions.

## Run FrameLink

Double-click `Start-FrameLink.bat` or `FrameLink-Trial15.exe`. The app includes scrcpy for Windows x64.

## Issue a license

1. Open `Start-License-Admin.bat` (or `FrameLink-License-Admin.exe`).
2. Choose **1 month (30 days)** or **1 year (365 days)** and generate a code.
3. Copy the code to the customer.
4. In FrameLink, click **License · Activate**, paste the code, and activate. The duration starts at activation.

**Keep `FrameLink-License-Admin.exe` private.** It contains the license signing key; do not distribute it with the customer app. This build validates signed codes locally and does not require an internet connection. It does not prevent customers from sharing codes or changing their system clock.

Camera resolution and frame rate are selected on the OBS setup page. Screen preview uses native phone resolution.


