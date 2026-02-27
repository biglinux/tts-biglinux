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
    switch.update_property(
        [Gtk.AccessibleProperty.LABEL], [acc_name]
    )

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
) -> tuple[Adw.ActionRow, Gtk.Scale]:
    """Create an action row with a horizontal scale slider."""
    row = Adw.ActionRow()

    if title_size_group:
        # Custom title area with SizeGroup for alignment
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title_box.set_valign(Gtk.Align.CENTER)
        title_box.set_spacing(2)

        title_label = Gtk.Label(label=title, xalign=0)
        title_label.add_css_class("title")
        title_box.append(title_label)

        if subtitle:
            sub_label = Gtk.Label(label=subtitle, xalign=0)
            sub_label.add_css_class("dim-label")
            sub_label.set_css_classes(["dim-label", "caption"])
            title_box.append(sub_label)

        row.add_prefix(title_box)
        title_size_group.add_widget(title_box)
    else:
        row.set_title(title)
        if subtitle:
            row.set_subtitle(subtitle)

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
    scale.set_size_request(280, -1)
    scale.set_valign(Gtk.Align.CENTER)

    # Accessibility
    acc_name = accessible_name or title
    scale.update_property(
        [Gtk.AccessibleProperty.LABEL], [acc_name]
    )

    if marks:
        for mark_value, mark_label in marks:
            scale.add_mark(mark_value, Gtk.PositionType.BOTTOM, mark_label)

    if on_changed:
        scale.connect("value-changed", lambda s: on_changed(s.get_value()))

    row.add_suffix(scale)
    return row, scale


def create_combo_row(
    title: str,
    subtitle: str | None = None,
    options: list[str] | None = None,
    selected_index: int = 0,
    on_selected: Callable[[int], None] | None = None,
    accessible_name: str | None = None,
) -> Adw.ComboRow:
    """Create a combo row with dropdown options."""
    row = Adw.ComboRow()
    row.set_title(title)
    if subtitle:
        row.set_subtitle(subtitle)

    if options:
        model = Gtk.StringList.new(options)
        row.set_model(model)
        if 0 <= selected_index < len(options):
            row.set_selected(selected_index)

    # Accessibility
    if accessible_name:
        row.update_property(
            [Gtk.AccessibleProperty.LABEL], [accessible_name]
        )

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
        row.update_property(
            [Gtk.AccessibleProperty.LABEL], [accessible_name]
        )

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
    button.update_property(
        [Gtk.AccessibleProperty.LABEL], [acc_name]
    )

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
    button.update_property(
        [Gtk.AccessibleProperty.LABEL], [acc_name]
    )

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
