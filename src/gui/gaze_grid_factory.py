# Chooses the gaze-grid implementation: the GPU/Win11 Qt overlay
# (default) or the legacy tkinter GazeGrid. Both share the same interface
# (open / update_cells / current_cell / note_action / close), so the gaze
# tools (launcher / clipboard / task switcher) are agnostic to which one
# they get.
import logging

from src.config_manager import ConfigManager

logger = logging.getLogger("GazeGridFactory")


def make_gaze_grid(tk_root):
    try:
        cfg = ConfigManager().config or {}
    except Exception:
        cfg = {}
    if cfg.get("qt_overlay", True):
        try:
            from src.gui.qt_overlay.qt_gaze_grid import QtGazeGrid
            return QtGazeGrid(tk_root)
        except Exception as e:
            logger.warning(
                "Qt overlay unavailable, falling back to tkinter grid: %s", e)
    from src.gui.gaze_grid import GazeGrid
    return GazeGrid(tk_root)
