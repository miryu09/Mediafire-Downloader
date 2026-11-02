# MediaFire Downloader (mf-dl)

Download MediaFire files and folders with a simple Windows app or the command line. Folder downloads include their contents.

## Before you start

The Windows app needs Python 3.14 and Microsoft Edge WebView2 Runtime. Python is not included in this project. The `run_gui.bat` launcher checks for these components and installs any that are missing.

To start the app, double-click `run_gui.vbs`. It runs `run_gui.bat` in the background, then opens the app in its own window. On first launch, the script also creates a project-local Python environment and installs the app's packages. An internet connection is required. Windows Package Manager (`winget`) must be available; if it is missing, install **App Installer** from the Microsoft Store. Windows may ask you to approve component installations.

You can also double-click the `MediaFire Downloader` shortcut included in this project to start the app; it launches `run_gui.vbs` for you. On first launch, the app opens maximized in a normal window; later launches use a regular window and remember its last position. The first launch can take a few minutes while Python and the required packages are installed; later launches should be faster. If you move or clone the project to a different location, use `run_gui.vbs` directly or update the shortcut to point to the new location.

## Start the Windows app

1. Download or clone this repository.
2. Double-click `run_gui.vbs` or `MediaFire Downloader` and wait for first-time setup to finish.
3. Paste a MediaFire link, choose where to save the files, and click **Download**. Use **Stop** to immediately terminate an active download.

The app opens in its own window and shows an animated loading screen until its local services and saved settings are ready. If it does not open, run `run_gui.bat` in a terminal to see the error. To install Python or WebView2 manually, use [Python downloads](https://www.python.org/downloads/) or the [WebView2 Runtime page](https://developer.microsoft.com/microsoft-edge/webview2/).
When a download starts, the app estimates the total size from MediaFire file metadata and displays it beside the download controls. Some files may not expose size metadata, in which case the app shows a partial estimate. During downloads, the activity panel shows a live graph for received network data, including its current rate, total, and numeric scale. Network throughput is measured over Wi-Fi or Ethernet; it is not the Wi-Fi connection's maximum link speed.

Temporary network errors are retried up to five times per item. Items that still fail are reported in Download activity.
The temporary `custom_folders.txt` lookup cache is removed when the download process exits.

## Automatic setup

When you start the Windows app with `run_gui.vbs` or `run_gui.bat`, its launcher checks for Python and WebView2, creates the project's `.venv` if needed, and installs the packages from `requirements.txt` on first setup or when that file changes. You do not need to install the app's Python packages manually. The first launch requires an internet connection and can take a few minutes.

This automatic setup applies to the Windows app launcher. Running `mfdl.py` directly from a terminal still requires Python and its packages to be installed in that Python environment.

## Other tools

- `web_crawler.py` visits website pages and saves MediaFire links it finds. Use it only on sites you are allowed to crawl.
- `analyze_mediafire.py` finds MediaFire links in text files.

## Credits and source

The original `mf-dl` project and downloader source are by **Pyxia**:

- **Main source repository:** [Pyxia/mf-dl on GitGud](https://gitgud.io/Pyxia/mf-dl/)

This repository builds on that project and includes a Windows desktop interface and related updates. Please refer to the original repository for the upstream source.

## License

GNU General Public License v3. See [`LICENSE`](./LICENSE).
