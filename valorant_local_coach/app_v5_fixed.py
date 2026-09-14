from __future__ import annotations

from app_v5 import App


def _walk_widgets(self, parent):
    """Yield all descendant Tk widgets safely, including nested frames."""
    try:
        children = parent.winfo_children()
    except Exception:
        return
    for child in children:
        yield child
        yield from _walk_widgets(self, child)


# Backward-compatible safety patch for v5 builds where the helper was omitted.
if not hasattr(App, "_walk_widgets"):
    App._walk_widgets = _walk_widgets


if __name__ == "__main__":
    App().mainloop()
