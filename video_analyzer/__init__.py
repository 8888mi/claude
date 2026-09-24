"""Анализ видеофрагментов на критичные артефакты по ТЗ проектов 86-87 / 88-89."""

__all__ = ["analyze_video", "AnalysisConfig"]


def __getattr__(name):
    # ленивый импорт: скачивание примеров работает без установленного OpenCV
    if name in __all__:
        from . import analyzer
        return getattr(analyzer, name)
    raise AttributeError(name)
