"""
Reusable UI components for BigLinux TTS.

Factory functions for consistent Adwaita widgets with full accessibility.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk


def create_preferences_group(
    title: str,
    description: str | None = None,
) -> Adw.PreferencesGroup:
    """Create a preferences group with title and optional description."""
    group = Adw.PreferencesGroup()
    group.set_title(title)
    if description:
        group.set_description(description)
    return group


def create_action_row_with_switch(
    title: str,
    subtitle: str | None = None,
    active: bool = False,
    on_toggled: Callable[[bool], None] | None = None,
    accessible_name: str | None = None,
) -> tuple[Adw.ActionRow, Gtk.Switch]:
    """Create an action row with a switch. Full keyboard + Orca support."""
    row = Adw.ActionRow()
    row.set_title(title)
    if subtitle:
        row.set_subtitle(subtitle)

    switch = Gtk.Switch()
    switch.set_active(active)
    switch.set_valign(Gtk.Align.CENTER)

    # Accessibility
    acc_name = accessible_name or title
    switch.update_property([Gtk.AccessibleProperty.LABEL], [acc_name])

    if on_toggled:
        switch.connect("notify::active", lambda s, _: on_toggled(s.get_active()))

    row.add_suffix(switch)
    row.set_activatable_widget(switch)
    return row, switch


def create_action_row_with_scale(
    title: str,
    subtitle: str | None = None,
    min_value: float = 0.0,
    max_value: float = 100.0,
    value: float = 50.0,
    step: float = 1.0,
    digits: int = 0,
    on_changed: Callable[[float], None] | None = None,
    marks: list[tuple[float, str]] | None = None,
    accessible_name: str | None = None,
    title_size_group: Gtk.SizeGroup | None = None,
) -> tuple[Adw.PreferencesRow, Gtk.Scale]:
    """Create a preferences row with a title/subtitle above a full-width scale.

    The scale is stacked BELOW the title (vertical layout) so it renders cleanly
    in a narrow settings sidebar instead of being squeezed beside the title.
    The returned row exposes `_title_label` / `_subtitle_label` so callers can
    relabel it later (e.g. Pitch → Expressiveness per backend).
    """
    row = Adw.PreferencesRow()
    row.set_activatable(False)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    box.set_margin_top(10)
    box.set_margin_bottom(6)
    box.set_margin_start(12)
    box.set_margin_end(12)

    title_label = Gtk.Label(label=title, xalign=0)
    title_label.add_css_class("heading")
    title_label.set_halign(Gtk.Align.START)
    box.append(title_label)
    row._title_label = title_label  # type: ignore[attr-defined]

    sub_label = Gtk.Label(label=subtitle or "", xalign=0)
    sub_label.set_css_classes(["dim-label", "caption"])
    sub_label.set_halign(Gtk.Align.START)
    sub_label.set_wrap(True)
    sub_label.set_visible(bool(subtitle))
    box.append(sub_label)
    row._subtitle_label = sub_label  # type: ignore[attr-defined]

    adjustment = Gtk.Adjustment(
        value=value,
        lower=min_value,
        upper=max_value,
        step_increment=step,
        page_increment=step * 10,
    )

    scale = Gtk.Scale(
        orientation=Gtk.Orientation.HORIZONTAL,
        adjustment=adjustment,
    )
    scale.set_digits(digits)
    scale.set_hexpand(True)
    scale.set_draw_value(False)
    scale.set_margin_top(2)

    acc_name = accessible_name or title
    scale.update_property([Gtk.AccessibleProperty.LABEL], [acc_name])

    if marks:
        for mark_value, mark_label in marks:
            scale.add_mark(mark_value, Gtk.PositionType.BOTTOM, mark_label)

    if on_changed:
        scale.connect("value-changed", lambda s: on_changed(s.get_value()))

    box.append(scale)
    row.set_child(box)
    return row, scale


def create_combo_row(
    title: str,
    subtitle: str | None = None,
    options: list[str] | None = None,
    selected_index: int = 0,
    on_selected: Callable[[int], None] | None = None,
    accessible_name: str | None = None,
) -> Adw.ComboRow:
    """Create a combo row with dropdown options and tooltip on items."""
    row = Adw.ComboRow()
    row.set_title(title)
    if subtitle:
        row.set_subtitle(subtitle)

    if options:
        model = Gtk.StringList.new(options)
        row.set_model(model)
        if 0 <= selected_index < len(options):
            row.set_selected(selected_index)

    # Custom factory: show full text with ellipsize + tooltip
    factory = Gtk.SignalListItemFactory()

    def _setup(f: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        label = Gtk.Label(xalign=0, ellipsize=3)  # PANGO_ELLIPSIZE_END = 3
        label.set_max_width_chars(60)
        label.set_hexpand(True)
        item.set_child(label)

    def _bind(f: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        label = item.get_child()
        string_obj = item.get_item()
        text = string_obj.get_string()
        label.set_text(text)
        label.set_tooltip_text(text)

    factory.connect("setup", _setup)
    factory.connect("bind", _bind)
    row.set_factory(factory)

    # Also set a separate list factory for the popup (wider)
    list_factory = Gtk.SignalListItemFactory()

    def _list_setup(f: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        label = Gtk.Label(xalign=0, wrap=True, wrap_mode=0)  # PANGO_WRAP_WORD
        label.set_hexpand(True)
        item.set_child(label)

    def _list_bind(f: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        label = item.get_child()
        string_obj = item.get_item()
        text = string_obj.get_string()
        label.set_text(text)
        label.set_tooltip_text(text)

    list_factory.connect("setup", _list_setup)
    list_factory.connect("bind", _list_bind)
    row.set_list_factory(list_factory)

    # Accessibility
    if accessible_name:
        row.update_property([Gtk.AccessibleProperty.LABEL], [accessible_name])

    if on_selected:
        row.connect("notify::selected", lambda r, _: on_selected(r.get_selected()))

    return row


def create_spin_row(
    title: str,
    subtitle: str | None = None,
    min_value: float = 0.0,
    max_value: float = 100.0,
    value: float = 50.0,
    step: float = 1.0,
    digits: int = 0,
    on_changed: Callable[[float], None] | None = None,
    accessible_name: str | None = None,
) -> Adw.SpinRow:
    """Create a spin row with numeric input."""
    adjustment = Gtk.Adjustment(
        value=value,
        lower=min_value,
        upper=max_value,
        step_increment=step,
        page_increment=step * 10,
    )

    row = Adw.SpinRow()
    row.set_title(title)
    row.set_adjustment(adjustment)
    row.set_digits(digits)
    if subtitle:
        row.set_subtitle(subtitle)

    # Accessibility
    if accessible_name:
        row.update_property([Gtk.AccessibleProperty.LABEL], [accessible_name])

    if on_changed:
        row.connect("notify::value", lambda r, _: on_changed(r.get_value()))

    return row


def create_expander_row(
    title: str,
    subtitle: str | None = None,
    icon_name: str | None = None,
    enable_switch: bool = False,
    expanded: bool = False,
) -> Adw.ExpanderRow:
    """Create an expander row with optional enable switch."""
    row = Adw.ExpanderRow()
    row.set_title(title)
    row.set_expanded(expanded)
    row.set_enable_expansion(True)
    row.set_show_enable_switch(enable_switch)
    if subtitle:
        row.set_subtitle(subtitle)
    if icon_name:
        row.set_icon_name(icon_name)
    return row


def create_button_row(
    label: str,
    style_class: str | None = None,
    on_clicked: Callable[[], None] | None = None,
    accessible_name: str | None = None,
) -> Gtk.Button:
    """Create a styled button."""
    button = Gtk.Button(label=label)
    button.set_valign(Gtk.Align.CENTER)

    acc_name = accessible_name or label
    button.update_property([Gtk.AccessibleProperty.LABEL], [acc_name])

    if style_class:
        button.add_css_class(style_class)
    if on_clicked:
        button.connect("clicked", lambda _: on_clicked())
    return button


def create_icon_button(
    icon_name: str,
    tooltip: str | None = None,
    style_class: str | None = None,
    on_clicked: Callable[[], None] | None = None,
    accessible_name: str | None = None,
) -> Gtk.Button:
    """Create an icon-only button with tooltip."""
    button = Gtk.Button.new_from_icon_name(icon_name)
    button.set_valign(Gtk.Align.CENTER)

    if tooltip:
        button.set_tooltip_text(tooltip)

    # Accessibility — icon buttons MUST have accessible name
    acc_name = accessible_name or tooltip or icon_name
    button.update_property([Gtk.AccessibleProperty.LABEL], [acc_name])

    if style_class:
        button.add_css_class(style_class)
    else:
        button.add_css_class("flat")

    if on_clicked:
        button.connect("clicked", lambda _: on_clicked())

    return button


def create_status_page(
    icon_name: str,
    title: str,
    description: str | None = None,
) -> Adw.StatusPage:
    """Create a status page with icon, title, and description."""
    page = Adw.StatusPage()
    page.set_icon_name(icon_name)
    page.set_title(title)
    if description:
        page.set_description(description)
    return page
