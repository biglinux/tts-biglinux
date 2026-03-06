# BigLinux TTS - Audit and Refactoring Roadmap

## 1. File Inventory and Analysis Summary
Successfully created `.audit/files_read.txt` enumerating all 19 Python files. Automated tools (`ruff`, `mypy`, `vulture`, `radon`) were executed via an isolated virtual environment (`.audit/venv`).

### Metrics
- **Files Analyzed**: 19 Python files
- **Linting (ruff)**: 43 violations (9 auto-fixable). Main issues involve `logging-f-string` and line length.
- **Formatting (ruff format)**: 12 files need reformatting (1059 lines).
- **Type Checking (mypy)**: 82 errors, mostly involving missing imports (`gi` bindings) and `json.load`/`subprocess` types.
- **Complexity (radon)**: `ui/main_view.py` is the most complex (mostly due to DBus/KDE desktop file manipulation). `services/voice_manager.py` also has some complex functions (`discover_voices`).

## 2. Issues and Recommendations (Prioritized)

### Critical Priority (Do immediately)
*   **Adwaita Dark Theme Warning**: The warning `Using GtkSettings:gtk-application-prefer-dark-theme with libadwaita is unsupported.` is critical for native look-and-feel. **Fix**: Remove `Gtk.Settings.get_default().set_property('gtk-application-prefer-dark-theme', True)` from `application.py` and replace it with `Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_DARK)`.
*   **State Loop Bug Potential**: In `TTSApplication._on_window_close_request`, multiple dialogs or callbacks might cause bugs due to the GTK main loop blocking. Needs careful verification.

### High Priority
*   **Code Formatting & Linting**: Apply `ruff format` and `ruff check --fix` to enforce a unified codestyle across all files. It ensures consistency before doing any complex structural refactoring.
*   **Type Hint Accuracy**: Correct `mypy` issues. By adding `# type: ignore` to `gi` repository imports, and strictly typed variables for `subprocess.Popen` and JSON parsing.

### Medium Priority (Architecture & UX)
*   **Separation of Concerns (KDE Integration)**: The `ui/main_view.py` contains almost 200 lines dealing purely with KDE desktop files, DBus calls to `kglobalaccel`, X11 vs Wayland bindings, and Plasma launchers. This is a severe violation of MVC/MVVM. **Fix**: Extract all shortcut and desktop interaction into a `services/desktop_integration_service.py`.
*   **Tray Service Lifecycle**: Ensure that `tray_service.py` completely terminates its thread upon application close. Some Qt/PySide subprocesses tend to hang if not closed properly via `stdin.close()`.

### Low Priority (Tech Debt)
*   **Voice Manager Refactor**: `discover_voices()` does too much synchronously and spawns many subprocess calls to `spd-say` and `espeak-ng`. While currently async via `utils.async_utils`, it could be optimized.
*   **Dead Code Cleanup**: `vulture` flagged some unused variables and imports. Need to safely strip them.

## 3. Accessibility & Orca Compatibility Checklist
During the manual scan:
*   [x] **ActionRows**: Uses `Adw.ActionRow` correctly.
*   [x] **Accessible Labels**: Custom components like `create_action_row_with_switch` actively assign `Gtk.AccessibleProperty.LABEL` -> Good.
*   [x] **Scale Widgets**: `create_action_row_with_scale` maps `accessible_name` reliably.
*   [ ] **Hero Status Element**: `_hero_icon` and `_hero_title` act as visual feedback but might not be read by Orca dynamically when they change. **Fix**: Ensure `update_property([Gtk.AccessibleProperty.DESCRIPTION])` or use `Adw.ToastOverlay` effectively, keeping `status-page` conventions.
*   [ ] **Capture Keyboard Dialog**: The shortcut intercept dialog suppresses all Global shortcuts, but if an active Orca user hits `Escape`, Orca might not read the state. Need to verify Orca compatibility with raw `Gtk.EventControllerKey`.

## 4. Implementation Plan (Execution)

1.  **Phase 1: Format & Lint Cleanup**: Run `ruff check --fix` and `ruff format` to normalize the codebase.
2.  **Phase 2: Dark Theme Fix**: Address the Adwaita `gtk-application-prefer-dark-theme` warning in `application.py`.
3.  **Phase 3: Code Refactoring (Separation)**:
    *   Extract KDE DBus / Shortcut logic from `ui/main_view.py` into a static/service helper logic to declutter the View.
4.  **Phase 4: Orca / UX Enhancements**: Ensure dynamic labels receive `Gtk.AccessibleProperty.LABEL` updates if their function changes (e.g., the "Test Voice" button switching to "Stop").
5.  **Phase 5: Final Validation**: Re-run the app, verify GTK settings, Wayland/X11 clipboard, and shortcut capturing.
