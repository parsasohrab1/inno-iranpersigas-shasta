"""Shared plotting helpers for the validation pipelines.

setup(lang) returns (pyplot, T). T(text) shapes Persian text for matplotlib (right-to-left with joined
letters) when the optional packages arabic_reshaper + python-bidi are importable, otherwise text is
returned unchanged. Persian fonts are registered from `font_dir` when it contains TTF files.
"""
import os


def _identity(s):
    return s


def setup(lang: str = "fa", shaping_lib: str | None = None, font_dir: str | None = None):
    import matplotlib

    matplotlib.use("Agg")
    shaping_lib = shaping_lib or os.environ.get("LCIS_FA_LIB", "")
    font_dir = font_dir or os.environ.get("LCIS_FA_FONT_DIR", "")
    T = _identity
    family = "DejaVu Sans"
    if lang == "fa":
        if shaping_lib and os.path.isdir(shaping_lib) and shaping_lib not in os.sys.path:
            os.sys.path.insert(0, shaping_lib)
        try:
            import arabic_reshaper
            from bidi.algorithm import get_display

            def T(s):  # noqa: F811
                return get_display(arabic_reshaper.reshape(str(s)))
        except Exception:  # noqa: BLE001
            T = _identity
        import matplotlib.font_manager as fm

        names = []
        if font_dir and os.path.isdir(font_dir):
            for fn in sorted(os.listdir(font_dir)):
                if fn.lower().endswith((".ttf", ".otf")):
                    try:
                        fm.fontManager.addfont(os.path.join(font_dir, fn))
                        names.append(fm.FontProperties(fname=os.path.join(font_dir, fn)).get_name())
                    except Exception:  # noqa: BLE001
                        pass
        if names:
            family = names[0]
    import matplotlib.pyplot as plt

    plt.rcParams["font.family"] = family
    plt.rcParams["font.sans-serif"] = [family, "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 110
    return plt, T


def save(fig, path):
    fig.savefig(path, bbox_inches="tight")
    import matplotlib.pyplot as plt

    plt.close(fig)
    return path
