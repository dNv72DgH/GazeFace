# Qt Quick (PySide6) gaze overlay — a GPU-accelerated, Win11-styled
# replacement for the tkinter gaze_grid overlay. Runs as a separate
# process (overlay_main) so the Qt event loop never collides with the
# main app's tkinter loop; the Python side (QtGazeGrid) computes the gaze
# cell and pushes render commands over a pipe.
