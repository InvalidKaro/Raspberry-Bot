"""Physical OLED display service for HomePi."""

from display_service.theme_modern import install as _install_modern_theme
from display_service.animation_retro import install as _install_retro_animation

_install_modern_theme()
_install_retro_animation()
