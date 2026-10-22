[app]
title = Super File Plus
package.name = superfileplus
package.domain = org.example
source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,atlas,json,txt,md
version = 0.1.0
requirements = python3,kivy,kivymd,arabic-reshaper,python-bidi
orientation = portrait
fullscreen = 0

# Do not add broad storage permissions blindly. Android scoped storage / SAF
# integration should be implemented and tested before enabling filesystem access.

[buildozer]
log_level = 2
warn_on_root = 1
