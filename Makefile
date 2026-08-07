# Tunes — build helpers (icons, packages, system install)
#
# Usage:
#   make icons              # Linux hicolor icons in build/icons/
#   make icons-all          # Linux + macOS + Windows outputs
#   make deb                # Build a .deb package (output in dist/)
#   make rpm                # Build an .rpm package (output in dist/)
#   make packages           # Build .deb and .rpm (requires both toolchains)
#   sudo make install       # Install desktop entry and icons
#   make uninstall          # Remove installed files

APP_ID := tunes-player
DESKTOP_ID := tunes.player
PREFIX ?= /usr/local
DESTDIR ?=
PYTHON ?= python3

DESKTOP_SRC := data/$(DESKTOP_ID).desktop
ICON_SRC := data/icons/$(APP_ID).svg
ICON_BUILD := build/icons
ICON_DEST := $(DESTDIR)$(PREFIX)/share/icons/hicolor
DESKTOP_DEST := $(DESTDIR)$(PREFIX)/share/applications/$(DESKTOP_ID).desktop
LEGACY_DESKTOP_DEST := $(DESTDIR)$(PREFIX)/share/applications/$(APP_ID).desktop

.PHONY: icons icons-all install uninstall clean-icons deb rpm packages help

help:
	@echo "Targets:"
	@echo "  icons       Generate Linux hicolor icons (build/icons/)"
	@echo "  icons-all   Generate Linux, macOS, and Windows icon outputs"
	@echo "  deb         Build a .deb package (output in dist/)"
	@echo "  rpm         Build an .rpm package (output in dist/)"
	@echo "  packages    Build .deb and .rpm (requires both toolchains)"
	@echo "  install     Install desktop entry and generated icons"
	@echo "  uninstall   Remove installed desktop entry and icons"
	@echo "  clean-icons Remove build/icons/"

icons:
	$(PYTHON) scripts/generate_icons.py --platform linux

icons-all:
	$(PYTHON) scripts/generate_icons.py --platform all

deb:
	./tools/build-deb.sh

rpm:
	./tools/build-rpm.sh

packages:
	./tools/build-packages.sh

install: icons
	install -Dm644 $(DESKTOP_SRC) $(DESKTOP_DEST)
	cp -a $(ICON_BUILD)/hicolor/. $(ICON_DEST)/
	@if command -v gtk-update-icon-cache >/dev/null 2>&1; then \
		gtk-update-icon-cache -q -f -t $(ICON_DEST); \
	fi
	@echo "Installed $(APP_ID) to $(DESTDIR)$(PREFIX)"

uninstall:
	rm -f $(DESKTOP_DEST) $(LEGACY_DESKTOP_DEST)
	rm -f $(ICON_DEST)/scalable/apps/$(APP_ID).svg
	@for size in 16 22 24 32 48 64 128 256 512; do \
		rm -f $(ICON_DEST)/$${size}x$${size}/apps/$(APP_ID).png; \
	done
	@if command -v gtk-update-icon-cache >/dev/null 2>&1; then \
		gtk-update-icon-cache -q -f -t $(ICON_DEST); \
	fi
	@echo "Removed $(APP_ID) from $(DESTDIR)$(PREFIX)"

clean-icons:
	rm -rf $(ICON_BUILD)
