import flet as ft
import requests
import json
import os
import sys
import re
import shutil
import random
import threading
import asyncio
import webbrowser
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

APP_VERSION = "v0.4.1"


def get_data_dir():
    """Retorna %APPDATA%\\SoliunSteamHub e migra arquivos locais de testes anteriores se existirem."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    data_dir = os.path.join(base, "SoliunSteamHub")
    os.makedirs(data_dir, exist_ok=True)

    for fname in ["config.json", "library_cache.json", "backlog.json", "achievements_cache.json"]:
        target = os.path.join(data_dir, fname)
        if not os.path.exists(target) and os.path.exists(fname):
            try:
                shutil.copy2(fname, target)
            except Exception:
                pass
    return data_dir


DATA_DIR = get_data_dir()
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
CACHE_FILE = os.path.join(DATA_DIR, "library_cache.json")
BACKLOG_FILE = os.path.join(DATA_DIR, "backlog.json")
ACH_CACHE_FILE = os.path.join(DATA_DIR, "achievements_cache.json")


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def scan_local_steam_games():
    """Varre as pastas da Steam nos discos locais para identificar jogos instalados, tamanho e unidade."""
    installed = {}
    candidate_roots = [
        r"C:\Program Files (x86)\Steam",
        r"C:\Program Files\Steam",
    ]
    for drive in ["C", "D", "E", "F", "G"]:
        candidate_roots.append(f"{drive}:\\SteamLibrary")
        candidate_roots.append(f"{drive}:\\Games\\Steam")

    library_folders = set()
    for root in candidate_roots:
        if os.path.exists(os.path.join(root, "steamapps")):
            library_folders.add(os.path.join(root, "steamapps"))
        vdf_path = os.path.join(root, "steamapps", "libraryfolders.vdf")
        if os.path.exists(vdf_path):
            try:
                with open(vdf_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                paths = re.findall(r'"path"\s+"([^"]+)"', content)
                for p in paths:
                    clean_p = p.replace("\\\\", "\\")
                    steamapps = os.path.join(clean_p, "steamapps")
                    if os.path.exists(steamapps):
                        library_folders.add(steamapps)
            except Exception:
                pass

    for folder in library_folders:
        drive_letter = os.path.splitdrive(folder)[0].upper() or "PC"
        try:
            for fname in os.listdir(folder):
                if fname.startswith("appmanifest_") and fname.endswith(".acf"):
                    fpath = os.path.join(folder, fname)
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        acf = f.read()
                    appid_m = re.search(r'"appid"\s+"(\d+)"', acf)
                    size_m = re.search(r'"SizeOnDisk"\s+"(\d+)"', acf)
                    if appid_m:
                        appid = int(appid_m.group(1))
                        size_bytes = int(size_m.group(1)) if size_m else 0
                        installed[appid] = {"size": size_bytes, "drive": drive_letter}
        except Exception:
            pass

    return installed


def format_size_gb(size_bytes):
    if size_bytes <= 0:
        return "Instalado"
    gb = size_bytes / (1024 ** 3)
    if gb < 1:
        return f"{size_bytes / (1024 ** 2):.0f} MB"
    return f"{gb:.1f} GB"


def main(page: ft.Page):
    page.title = f"Soliun Steam Hub {APP_VERSION}"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = "#0E1117"
    page.window.width = 1260
    page.window.height = 830
    page.padding = 20

    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    icon_path = os.path.join(base_path, "icon.ico")
    if os.path.exists(icon_path):
        page.window.icon = icon_path

    config = load_json(CONFIG_FILE, {"api_key": "", "steam_id": ""})
    cache = load_json(CACHE_FILE, {"profile": {}, "games": []})
    backlog = load_json(BACKLOG_FILE, {})
    ach_cache = load_json(ACH_CACHE_FILE, {})
    installed_map = scan_local_steam_games()

    current_filter = "all"
    search_query = ""
    current_view = "library"

    # Controles de UI globais
    status_text = ft.Text("", size=13, color="#9BA3B0")
    loading_ring = ft.ProgressRing(width=18, height=18, stroke_width=2, visible=False)

    # Elementos do Perfil e Métricas
    avatar_img = ft.Image(
        src="https://avatars.steamstatic.com/fef49e7fa7e1997310d705b2a6158ff8dc1cdfeb_full.jpg",
        width=64,
        height=64,
        border_radius=32,
    )
    profile_name = ft.Text("Perfil não sincronizado", size=20, weight=ft.FontWeight.BOLD)
    profile_sub = ft.Text("Configure sua API Key e SteamID para iniciar", size=13, color="#9BA3B0")

    stat_total_games = ft.Text("0", size=20, weight=ft.FontWeight.BOLD, color="#38BDF8")
    stat_total_hours = ft.Text("0h", size=20, weight=ft.FontWeight.BOLD, color="#4ADE80")
    stat_unplayed = ft.Text("0", size=20, weight=ft.FontWeight.BOLD, color="#F87171")
    stat_installed = ft.Text("0", size=20, weight=ft.FontWeight.BOLD, color="#C084FC")

    games_grid = ft.GridView(
        expand=True,
        runs_count=5,
        max_extent=250,
        child_aspect_ratio=0.70,
        spacing=14,
        run_spacing=14,
    )

    stats_view = ft.Column(
        expand=True,
        scroll=ft.ScrollMode.AUTO,
        spacing=16,
        visible=False,
    )

    def show_toast(message, icon=ft.Icons.PLAY_CIRCLE_FILLED, icon_color="#4ADE80"):
        snack = ft.SnackBar(
            bgcolor="#1F2937",
            duration=3000,
            content=ft.Row(
                spacing=10,
                controls=[
                    ft.Icon(icon, color=icon_color, size=20),
                    ft.Text(message, color="#F3F4F6", size=13, weight=ft.FontWeight.W_500),
                ],
            ),
        )
        page.overlay.append(snack)
        snack.open = True
        page.update()

    def launch_steam_game(appid, game_name):
        webbrowser.open(f"steam://rungameid/{appid}")
        status_text.value = f"Iniciando '{game_name}' via Steam..."
        show_toast(f"Abrindo '{game_name}' na Steam...")

    def open_steam_store(appid, game_name):
        webbrowser.open(f"steam://store/{appid}")
        show_toast(f"Abrindo página da loja: {game_name}", ft.Icons.STOREFRONT, "#38BDF8")

    def open_steam_details(appid, game_name):
        webbrowser.open(f"steam://nav/games/details/{appid}")
        show_toast(f"Abrindo detalhes na Steam: {game_name}", ft.Icons.OPEN_IN_NEW, "#C084FC")

    def format_hours(minutes):
        if minutes == 0:
            return "Nunca jogado"
        hours = minutes / 60
        if hours < 1:
            return f"{minutes} min"
        return f"{hours:.1f} h"

    def update_stats_and_profile():
        prof = cache.get("profile", {})
        games = cache.get("games", [])

        if prof:
            avatar_img.src = prof.get("avatarfull", avatar_img.src)
            profile_name.value = prof.get("personaname", "Jogador Steam")
            state = prof.get("personastate", 0)
            states = {0: "Offline", 1: "Online", 2: "Ocupado", 3: "Ausente", 4: "Soneca"}
            current_game = prof.get("gameextrainfo")
            profile_sub.value = (
                f"Jogando agora: {current_game}"
                if current_game
                else f"Status: {states.get(state, 'Offline')}"
            )

        total_g = len(games)
        total_m = sum(g.get("playtime_forever", 0) for g in games)
        unplayed_g = sum(1 for g in games if g.get("playtime_forever", 0) == 0)

        installed_count = sum(1 for g in games if g["appid"] in installed_map)
        installed_bytes = sum(installed_map[g["appid"]]["size"] for g in games if g["appid"] in installed_map)

        stat_total_games.value = str(total_g)
        stat_total_hours.value = f"{int(total_m // 60):,}h".replace(",", ".")
        stat_unplayed.value = f"{unplayed_g} ({int((unplayed_g / total_g) * 100) if total_g else 0}%)"
        stat_installed.value = f"{installed_count} ({format_size_gb(installed_bytes)})"

    def rescan_disks_only(e=None):
        nonlocal installed_map
        installed_map = scan_local_steam_games()
        update_stats_and_profile()
        if current_view == "stats":
            render_stats()
            page.update()
        else:
            render_games()
        show_toast(f"Discos verificados: {len(installed_map)} jogos encontrados.", ft.Icons.STORAGE, "#C084FC")

    def set_game_status(appid, new_status):
        key = str(appid)
        if new_status == "none":
            backlog.pop(key, None)
        else:
            backlog[key] = new_status
        save_json(BACKLOG_FILE, backlog)
        if current_filter in ["queue", "playing", "completed", "dropped"]:
            render_games()
        elif current_view == "stats":
            render_stats()
            page.update()
        else:
            page.update()

    def open_achievements_dialog(appid, game_name):
        api_key = config.get("api_key", "").strip()
        steam_id = config.get("steam_id", "").strip()
        if not api_key or not steam_id:
            open_config_dialog()
            return

        cache_key = str(appid)
        is_closed = False

        content_box = ft.Column(
            width=480,
            height=380,
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[],
        )

        def build_achievements_ui(items_data, from_cache=False):
            if not items_data:
                content_box.alignment = ft.MainAxisAlignment.CENTER
                content_box.horizontal_alignment = ft.CrossAxisAlignment.CENTER
                content_box.controls = [
                    ft.Icon(ft.Icons.INFO_OUTLINE, color="#9BA3B0", size=34),
                    ft.Text(
                        "Este título não possui conquistas registradas ou os dados são privados.",
                        size=13,
                        color="#9BA3B0",
                        text_align=ft.TextAlign.CENTER,
                    ),
                ]
                return

            unlocked = [a for a in items_data if a.get("achieved")]
            locked = [a for a in items_data if not a.get("achieved")]
            total_a = len(items_data)
            unlocked_count = len(unlocked)
            pct = (unlocked_count / total_a) if total_a else 0

            rows = []
            for item in unlocked + locked:
                is_done = item.get("achieved", False)
                title = item.get("title", "")
                desc = item.get("desc", "")
                icon_url = item.get("icon", "")

                rows.append(
                    ft.Container(
                        bgcolor="#161B22" if is_done else "#11151C",
                        border=ft.Border.all(1, "#2E4033" if is_done else "#1F242D"),
                        border_radius=8,
                        padding=8,
                        content=ft.Row(
                            spacing=10,
                            controls=[
                                ft.Image(
                                    src=icon_url,
                                    width=38,
                                    height=38,
                                    border_radius=4,
                                    error_content=ft.Icon(
                                        ft.Icons.EMOJI_EVENTS if is_done else ft.Icons.LOCK_OUTLINE,
                                        color="#FACC15" if is_done else "#6B7280",
                                        size=24,
                                    ),
                                ),
                                ft.Column(
                                    expand=True,
                                    spacing=2,
                                    controls=[
                                        ft.Text(
                                            title,
                                            size=12,
                                            weight=ft.FontWeight.BOLD,
                                            color="#F3F4F6" if is_done else "#9CA3AF",
                                        ),
                                        ft.Text(
                                            desc,
                                            size=11,
                                            color="#9BA3B0" if is_done else "#6B7280",
                                            max_lines=2,
                                            overflow=ft.TextOverflow.ELLIPSIS,
                                        ),
                                    ],
                                ),
                                ft.Icon(
                                    ft.Icons.CHECK_CIRCLE if is_done else ft.Icons.RADIO_BUTTON_UNCHECKED,
                                    color="#4ADE80" if is_done else "#4B5563",
                                    size=18,
                                ),
                            ],
                        ),
                    )
                )

            source_tag = " (Cache local)" if from_cache else ""
            content_box.alignment = ft.MainAxisAlignment.START
            content_box.horizontal_alignment = ft.CrossAxisAlignment.STRETCH
            content_box.controls = [
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Text(
                            f"Desbloqueadas: {unlocked_count} de {total_a}{source_tag}",
                            size=13,
                            weight=ft.FontWeight.BOLD,
                            color="#FACC15",
                        ),
                        ft.Text(
                            f"{pct * 100:.1f}%",
                            size=13,
                            weight=ft.FontWeight.BOLD,
                            color="#4ADE80",
                        ),
                    ],
                ),
                ft.ProgressBar(value=pct, color="#FACC15", bgcolor="#1F2937", height=8, border_radius=4),
                ft.Divider(height=6, color="#262C36"),
                ft.Column(expand=True, scroll=ft.ScrollMode.AUTO, spacing=6, controls=rows),
            ]

        def _sync_http_fetch():
            p_url = (
                f"https://api.steampowered.com/ISteamUserStats/GetPlayerAchievements/v0001/"
                f"?appid={appid}&key={api_key}&steamid={steam_id}&l=brazilian"
            )
            s_url = (
                f"https://api.steampowered.com/ISteamUserStats/GetSchemaForGame/v0002/"
                f"?key={api_key}&appid={appid}&l=brazilian"
            )
            with requests.Session() as session:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    f_player = executor.submit(session.get, p_url, timeout=8)
                    f_schema = executor.submit(session.get, s_url, timeout=8)
                    r_player = f_player.result().json()
                    r_schema = f_schema.result().json()

            player_stats = r_player.get("playerstats", {})
            if not player_stats.get("success") or not player_stats.get("achievements"):
                return []

            schema_list = (
                r_schema.get("game", {})
                .get("availableGameStats", {})
                .get("achievements", [])
            )
            schema_map = {item["name"]: item for item in schema_list}

            normalized = []
            for item in player_stats["achievements"]:
                apiname = item.get("apiname", "")
                is_done = item.get("achieved") == 1
                meta = schema_map.get(apiname, {})
                normalized.append({
                    "achieved": is_done,
                    "title": meta.get("displayName") or item.get("name") or apiname,
                    "desc": meta.get("description") or ("Conquista secreta" if meta.get("hidden") else ""),
                    "icon": (meta.get("icon") if is_done else meta.get("icongray")) or "",
                })
            return normalized

        async def load_achievements_task(force_refresh=False):
            if not force_refresh and cache_key in ach_cache:
                build_achievements_ui(ach_cache[cache_key], from_cache=True)
                page.update()
                return

            content_box.alignment = ft.MainAxisAlignment.CENTER
            content_box.horizontal_alignment = ft.CrossAxisAlignment.CENTER
            content_box.controls = [
                ft.ProgressRing(width=28, height=28),
                ft.Text("Consultando conquistas na Steam...", size=13, color="#9BA3B0"),
            ]
            page.update()

            try:
                normalized = await asyncio.to_thread(_sync_http_fetch)
                if is_closed:
                    return
                ach_cache[cache_key] = normalized
                save_json(ACH_CACHE_FILE, ach_cache)
                build_achievements_ui(normalized, from_cache=False)
            except Exception as ex:
                if is_closed:
                    return
                content_box.alignment = ft.MainAxisAlignment.CENTER
                content_box.horizontal_alignment = ft.CrossAxisAlignment.CENTER
                content_box.controls = [
                    ft.Text(f"Erro ao carregar conquistas: {ex}", color="#F87171", size=12)
                ]
            page.update()

        def close_dlg(_=None):
            nonlocal is_closed
            is_closed = True
            dlg.open = False
            page.update()

        def refresh_clicked(_):
            page.run_task(load_achievements_task, True)

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                spacing=8,
                controls=[
                    ft.Icon(ft.Icons.EMOJI_EVENTS, color="#FACC15"),
                    ft.Text(f"Conquistas: {game_name}", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ],
            ),
            content=content_box,
            actions=[
                ft.TextButton("Atualizar", icon=ft.Icons.REFRESH, on_click=refresh_clicked),
                ft.TextButton("Fechar", on_click=close_dlg),
            ],
        )
        page.overlay.append(dlg)
        dlg.open = True

        if cache_key in ach_cache:
            build_achievements_ui(ach_cache[cache_key], from_cache=True)
            page.update()
        else:
            page.run_task(load_achievements_task, False)

    def render_stats():
        games = cache.get("games", [])
        total_g = len(games)
        if total_g == 0:
            stats_view.controls = [
                ft.Text("Sincronize sua biblioteca para visualizar as estatísticas.", color="#9BA3B0")
            ]
            return

        played_games = [g for g in games if g.get("playtime_forever", 0) > 0]
        unplayed_count = total_g - len(played_games)
        total_mins = sum(g.get("playtime_forever", 0) for g in games)
        total_hours = total_mins / 60
        avg_hours_played = (total_hours / len(played_games)) if played_games else 0
        engagement_pct = (len(played_games) / total_g) * 100

        sorted_by_time = sorted(games, key=lambda x: x.get("playtime_forever", 0), reverse=True)
        top_10 = [g for g in sorted_by_time[:10] if g.get("playtime_forever", 0) > 0]
        max_mins = top_10[0]["playtime_forever"] if top_10 else 1
        top_game_name = top_10[0].get("name", "-") if top_10 else "Nenhum"
        top_game_hours = f"{top_10[0]['playtime_forever'] / 60:.1f}h" if top_10 else "0h"

        def kpi_box(icon, label, value, sub, color):
            return ft.Container(
                expand=True,
                bgcolor="#161B22",
                border=ft.Border.all(1, "#262C36"),
                border_radius=10,
                padding=14,
                content=ft.Column(
                    spacing=4,
                    controls=[
                        ft.Row(
                            spacing=6,
                            controls=[
                                ft.Icon(icon, size=16, color=color),
                                ft.Text(label, size=12, color="#9BA3B0"),
                            ],
                        ),
                        ft.Text(
                            value,
                            size=20,
                            weight=ft.FontWeight.BOLD,
                            color=color,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        ft.Text(sub, size=11, color="#6B7280"),
                    ],
                ),
            )

        kpi_row = ft.Row(
            spacing=12,
            controls=[
                kpi_box(
                    ft.Icons.PIE_CHART,
                    "Taxa de Aproveitamento",
                    f"{engagement_pct:.1f}%",
                    f"{len(played_games)} jogados vs. {unplayed_count} intactos",
                    "#38BDF8",
                ),
                kpi_box(
                    ft.Icons.AV_TIMER,
                    "Média por Jogo Aberto",
                    f"{avg_hours_played:.1f} h",
                    f"Total acumulado: {int(total_hours):,}h".replace(",", "."),
                    "#4ADE80",
                ),
                kpi_box(
                    ft.Icons.EMOJI_EVENTS,
                    "Rei da Biblioteca",
                    top_game_name,
                    f"{top_game_hours} registradas na Steam",
                    "#FACC15",
                ),
                kpi_box(
                    ft.Icons.BOOKMARK_ADDED,
                    "Backlog Catalogado",
                    f"{len(backlog)} jogos",
                    f"{sum(1 for v in backlog.values() if v == 'completed')} zerados marcados",
                    "#C084FC",
                ),
            ],
        )

        top_bars = []
        for idx, g in enumerate(top_10, start=1):
            mins = g.get("playtime_forever", 0)
            hrs = mins / 60
            ratio = mins / max_mins
            bar_color = "#4ADE80" if idx == 1 else ("#38BDF8" if idx <= 3 else "#60A5FA")

            top_bars.append(
                ft.Column(
                    spacing=4,
                    controls=[
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            controls=[
                                ft.Text(
                                    f"#{idx}  {g.get('name', '')}",
                                    size=12,
                                    weight=ft.FontWeight.W_600,
                                    max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                ),
                                ft.Text(f"{hrs:.1f} h", size=12, color=bar_color, weight=ft.FontWeight.BOLD),
                            ],
                        ),
                        ft.ProgressBar(
                            value=ratio,
                            color=bar_color,
                            bgcolor="#1F2937",
                            height=8,
                            border_radius=4,
                        ),
                    ],
                )
            )

        top_10_panel = ft.Container(
            expand=3,
            bgcolor="#161B22",
            border=ft.Border.all(1, "#262C36"),
            border_radius=10,
            padding=16,
            content=ft.Column(
                spacing=12,
                controls=[
                    ft.Row(
                        spacing=8,
                        controls=[
                            ft.Icon(ft.Icons.BAR_CHART, color="#4ADE80", size=18),
                            ft.Text("Top 10 Jogos Mais Jogados", size=15, weight=ft.FontWeight.BOLD),
                        ],
                    ),
                    ft.Divider(height=1, color="#262C36"),
                    *(top_bars if top_bars else [ft.Text("Nenhum jogo com tempo registrado.", color="#9BA3B0")]),
                ],
            ),
        )

        status_meta = [
            ("completed", "Zerados", "#4ADE80"),
            ("playing", "Jogando", "#FACC15"),
            ("queue", "Na Fila", "#38BDF8"),
            ("dropped", "Abandonados", "#F87171"),
            ("none", "Sem status", "#6B7280"),
        ]
        counts = defaultdict(int)
        for g in games:
            st = backlog.get(str(g["appid"]), "none")
            counts[st] += 1

        backlog_bars = []
        for st_key, label, color in status_meta:
            c = counts[st_key]
            pct = (c / total_g) if total_g else 0
            backlog_bars.append(
                ft.Column(
                    spacing=4,
                    controls=[
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            controls=[
                                ft.Text(label, size=12, color="#E5E7EB"),
                                ft.Text(f"{c} ({pct * 100:.0f}%)", size=12, color=color, weight=ft.FontWeight.BOLD),
                            ],
                        ),
                        ft.ProgressBar(
                            value=pct,
                            color=color,
                            bgcolor="#1F2937",
                            height=7,
                            border_radius=4,
                        ),
                    ],
                )
            )

        drive_sizes = defaultdict(int)
        drive_counts = defaultdict(int)
        for g in games:
            info = installed_map.get(g["appid"])
            if info:
                drive_sizes[info["drive"]] += info["size"]
                drive_counts[info["drive"]] += 1

        total_disk_bytes = sum(drive_sizes.values()) or 1
        drive_rows = []
        for drv in sorted(drive_sizes.keys()):
            sz = drive_sizes[drv]
            cnt = drive_counts[drv]
            ratio = sz / total_disk_bytes
            drive_rows.append(
                ft.Column(
                    spacing=4,
                    controls=[
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            controls=[
                                ft.Text(f"Unidade {drv}\\ ({cnt} jogos)", size=12, color="#E5E7EB"),
                                ft.Text(format_size_gb(sz), size=12, color="#C084FC", weight=ft.FontWeight.BOLD),
                            ],
                        ),
                        ft.ProgressBar(
                            value=ratio,
                            color="#C084FC",
                            bgcolor="#1F2937",
                            height=7,
                            border_radius=4,
                        ),
                    ],
                )
            )

        right_column = ft.Column(
            expand=2,
            spacing=14,
            controls=[
                ft.Container(
                    bgcolor="#161B22",
                    border=ft.Border.all(1, "#262C36"),
                    border_radius=10,
                    padding=16,
                    content=ft.Column(
                        spacing=10,
                        controls=[
                            ft.Row(
                                spacing=8,
                                controls=[
                                    ft.Icon(ft.Icons.LABEL, color="#38BDF8", size=18),
                                    ft.Text("Distribuição do Backlog", size=15, weight=ft.FontWeight.BOLD),
                                ],
                            ),
                            ft.Divider(height=1, color="#262C36"),
                            *backlog_bars,
                        ],
                    ),
                ),
                ft.Container(
                    bgcolor="#161B22",
                    border=ft.Border.all(1, "#262C36"),
                    border_radius=10,
                    padding=16,
                    content=ft.Column(
                        spacing=10,
                        controls=[
                            ft.Row(
                                spacing=8,
                                controls=[
                                    ft.Icon(ft.Icons.STORAGE, color="#C084FC", size=18),
                                    ft.Text("Espaço Steam por Disco", size=15, weight=ft.FontWeight.BOLD),
                                ],
                            ),
                            ft.Divider(height=1, color="#262C36"),
                            *(drive_rows if drive_rows else [ft.Text("Nenhum jogo local detectado.", size=12, color="#9BA3B0")]),
                        ],
                    ),
                ),
            ],
        )

        stats_view.controls = [
            kpi_row,
            ft.Row(
                vertical_alignment=ft.CrossAxisAlignment.START,
                spacing=14,
                controls=[top_10_panel, right_column],
            ),
        ]

    def render_games():
        games = cache.get("games", [])
        filtered = []

        for g in games:
            appid = g["appid"]
            name = g.get("name", "")
            mins = g.get("playtime_forever", 0)
            b_status = backlog.get(str(appid), "none")
            is_inst = appid in installed_map

            if search_query and search_query.lower() not in name.lower():
                continue
            if current_filter == "unplayed" and mins > 0:
                continue
            if current_filter == "played" and mins == 0:
                continue
            if current_filter == "installed" and not is_inst:
                continue
            if current_filter in ["queue", "playing", "completed", "dropped"] and b_status != current_filter:
                continue

            filtered.append(g)

        if current_filter == "most_played":
            filtered.sort(key=lambda x: x.get("playtime_forever", 0), reverse=True)
        elif current_filter == "installed":
            filtered.sort(key=lambda x: installed_map.get(x["appid"], {}).get("size", 0), reverse=True)
        else:
            filtered.sort(key=lambda x: x.get("name", "").lower())

        cards = []
        for g in filtered:
            appid = g["appid"]
            name = g.get("name", f"App {appid}")
            mins = g.get("playtime_forever", 0)
            b_status = backlog.get(str(appid), "none")
            is_inst = appid in installed_map
            header_url = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"

            badge_color = "#EF4444" if mins == 0 else "#22C55E"

            cover_control = ft.Container(
                border_radius=6,
                tooltip="Clique para jogar ou instalar na Steam",
                on_click=lambda e, aid=appid, gname=name: launch_steam_game(aid, gname),
                content=ft.Stack(
                    controls=[
                        ft.Image(
                            src=header_url,
                            width=230,
                            height=105,
                            border_radius=6,
                            fit=ft.BoxFit.COVER,
                            error_content=ft.Container(
                                bgcolor="#1F2937",
                                height=105,
                                border_radius=6,
                                alignment=ft.Alignment.CENTER,
                                content=ft.Icon(ft.Icons.VIDEOGAME_ASSET, color="#6B7280"),
                            ),
                        ),
                        ft.Container(
                            visible=is_inst,
                            right=6,
                            top=6,
                            bgcolor="#CC111827",
                            border=ft.Border.all(1, "#A855F7"),
                            border_radius=5,
                            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                            content=ft.Row(
                                spacing=3,
                                tight=True,
                                controls=[
                                    ft.Icon(ft.Icons.STORAGE, size=11, color="#C084FC"),
                                    ft.Text(
                                        format_size_gb(installed_map[appid]["size"]) if is_inst else "",
                                        size=10,
                                        color="#E9D5FF",
                                        weight=ft.FontWeight.BOLD,
                                    ),
                                ],
                            ),
                        ),
                    ]
                ),
            )

            status_dropdown = ft.Dropdown(
                value=b_status,
                text_size=11,
                dense=True,
                content_padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                options=[
                    ft.dropdown.Option("none", "Sem status"),
                    ft.dropdown.Option("queue", "Na Fila"),
                    ft.dropdown.Option("playing", "Jogando"),
                    ft.dropdown.Option("completed", "Zerado"),
                    ft.dropdown.Option("dropped", "Abandonado"),
                ],
                on_select=lambda e, aid=appid: set_game_status(aid, e.control.value),
            )

            action_buttons = ft.Row(
                spacing=2,
                tight=True,
                controls=[
                    ft.IconButton(
                        icon=ft.Icons.EMOJI_EVENTS,
                        icon_size=16,
                        icon_color="#FACC15",
                        tooltip="Ver Conquistas",
                        padding=4,
                        on_click=lambda e, aid=appid, gname=name: open_achievements_dialog(aid, gname),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.STOREFRONT,
                        icon_size=16,
                        icon_color="#38BDF8",
                        tooltip="Abrir na Loja Steam",
                        padding=4,
                        on_click=lambda e, aid=appid, gname=name: open_steam_store(aid, gname),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.OPEN_IN_NEW,
                        icon_size=16,
                        icon_color="#9BA3B0",
                        tooltip="Detalhes na Biblioteca Steam",
                        padding=4,
                        on_click=lambda e, aid=appid, gname=name: open_steam_details(aid, gname),
                    ),
                ],
            )

            cards.append(
                ft.Container(
                    bgcolor="#161B22",
                    border_radius=10,
                    border=ft.Border.all(1, "#3B2D54" if is_inst else "#262C36"),
                    padding=10,
                    content=ft.Column(
                        spacing=6,
                        controls=[
                            cover_control,
                            ft.Text(
                                name,
                                size=13,
                                weight=ft.FontWeight.W_600,
                                max_lines=2,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Container(expand=True),
                            status_dropdown,
                            ft.Row(
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                controls=[
                                    ft.Container(
                                        bgcolor="#1F2937",
                                        padding=ft.Padding.symmetric(horizontal=7, vertical=3),
                                        border_radius=6,
                                        content=ft.Text(
                                            format_hours(mins),
                                            size=11,
                                            color=badge_color,
                                            weight=ft.FontWeight.BOLD,
                                        ),
                                    ),
                                    action_buttons,
                                ],
                            ),
                        ],
                    ),
                )
            )

        games_grid.controls = cards
        status_text.value = f"Exibindo {len(filtered)} de {len(games)} jogos"
        page.update()

    def switch_view(target_view):
        nonlocal current_view
        current_view = target_view
        is_lib = target_view == "library"

        games_grid.visible = is_lib
        stats_view.visible = not is_lib
        search_field.visible = is_lib
        filter_dropdown.visible = is_lib

        btn_lib_view.style = ft.ButtonStyle(bgcolor="#2563EB" if is_lib else "#161B22")
        btn_stats_view.style = ft.ButtonStyle(bgcolor="#2563EB" if not is_lib else "#161B22")

        if not is_lib:
            render_stats()
            status_text.value = "Painel analítico da biblioteca"
        else:
            render_games()
            return
        page.update()

    def open_roulette_dialog(e=None):
        games = cache.get("games", [])
        if not games:
            return

        candidates = [
            g for g in games
            if backlog.get(str(g["appid"])) == "queue" or g.get("playtime_forever", 0) == 0
        ]
        pool_label = "da sua Fila / Nunca Jogados"
        if not candidates:
            candidates = games
            pool_label = "de toda a sua Biblioteca"

        chosen = random.choice(candidates)
        appid = chosen["appid"]
        name = chosen.get("name", f"App {appid}")
        mins = chosen.get("playtime_forever", 0)
        is_inst = appid in installed_map
        header_url = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"

        def close_dlg(_=None):
            dlg.open = False
            page.update()

        def reroll(_):
            close_dlg()
            open_roulette_dialog()

        def play_now(_):
            close_dlg()
            launch_steam_game(appid, name)

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                spacing=8,
                controls=[
                    ft.Icon(ft.Icons.CASINO, color="#38BDF8"),
                    ft.Text("Roleta: O que jogar agora?"),
                ],
            ),
            content=ft.Column(
                tight=True,
                width=420,
                spacing=12,
                controls=[
                    ft.Text(f"Sorteado {pool_label}:", size=12, color="#9BA3B0"),
                    ft.Image(
                        src=header_url,
                        width=420,
                        height=196,
                        border_radius=8,
                        fit=ft.BoxFit.COVER,
                    ),
                    ft.Text(name, size=18, weight=ft.FontWeight.BOLD),
                    ft.Row(
                        spacing=12,
                        controls=[
                            ft.Text(f"Tempo: {format_hours(mins)}", size=12, color="#4ADE80"),
                            ft.Text(
                                f"SSD {installed_map[appid]['drive']}\\: {format_size_gb(installed_map[appid]['size'])}"
                                if is_inst
                                else "Não instalado no PC",
                                size=12,
                                color="#C084FC" if is_inst else "#9BA3B0",
                            ),
                        ],
                    ),
                ],
            ),
            actions=[
                ft.TextButton("Fechar", on_click=close_dlg),
                ft.Button("Sortear Outro", icon=ft.Icons.REFRESH, on_click=reroll),
                ft.Button("Jogar Agora", icon=ft.Icons.PLAY_ARROW, on_click=play_now),
            ],
        )
        page.overlay.append(dlg)
        dlg.open = True
        page.update()

    def sync_steam_data(e=None):
        nonlocal installed_map
        api_key = config.get("api_key", "").strip()
        steam_id = config.get("steam_id", "").strip()

        if not api_key or not steam_id:
            open_config_dialog()
            return

        def worker():
            nonlocal installed_map
            loading_ring.visible = True
            status_text.value = "Sincronizando Steam e escaneando discos locais..."
            page.update()

            try:
                installed_map = scan_local_steam_games()

                prof_url = (
                    f"https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/"
                    f"?key={api_key}&steamids={steam_id}"
                )
                r_prof = requests.get(prof_url, timeout=15).json()
                players = r_prof.get("response", {}).get("players", [])
                if players:
                    cache["profile"] = players[0]

                lib_url = (
                    f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/"
                    f"?key={api_key}&steamid={steam_id}&include_appinfo=true&include_played_free_games=true&format=json"
                )
                r_lib = requests.get(lib_url, timeout=15).json()
                games = r_lib.get("response", {}).get("games", [])

                if not games:
                    status_text.value = "Nenhum jogo retornado. Verifique se 'Detalhes dos jogos' está Público na Steam."
                else:
                    cache["games"] = games
                    save_json(CACHE_FILE, cache)
                    update_stats_and_profile()
                    if current_view == "stats":
                        render_stats()
                    else:
                        render_games()
                    status_text.value = f"Sincronizado! {len(games)} jogos ({len(installed_map)} instalados)."

            except Exception as ex:
                status_text.value = f"Erro ao conectar na Steam: {ex}"
            finally:
                loading_ring.visible = False
                page.update()

        threading.Thread(target=worker, daemon=True).start()

    def open_config_dialog(e=None):
        key_field = ft.TextField(
            label="Steam Web API Key",
            value=config.get("api_key", ""),
            password=True,
            can_reveal_password=True,
        )
        id_field = ft.TextField(
            label="SteamID64 (17 dígitos)",
            value=config.get("steam_id", ""),
            hint_text="Ex: 76561198000000000",
        )

        def save_and_close(ev):
            config["api_key"] = key_field.value.strip()
            config["steam_id"] = id_field.value.strip()
            save_json(CONFIG_FILE, config)
            dlg.open = False
            page.update()
            sync_steam_data()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                spacing=8,
                controls=[
                    ft.Icon(ft.Icons.SETTINGS, color="#38BDF8"),
                    ft.Text(f"Configurar Credenciais ({APP_VERSION})"),
                ],
            ),
            content=ft.Column(
                tight=True,
                width=470,
                spacing=12,
                controls=[
                    ft.Container(
                        bgcolor="#161B22",
                        border=ft.Border.all(1, "#262C36"),
                        border_radius=8,
                        padding=12,
                        content=ft.Column(
                            spacing=6,
                            controls=[
                                ft.Text("Como obter seus dados da Steam:", size=12, weight=ft.FontWeight.BOLD, color="#38BDF8"),
                                ft.Text(
                                    "1. Gere sua chave gratuita em steamcommunity.com/dev/apikey (use 'localhost' como domínio).",
                                    size=11,
                                    color="#D1D5DB",
                                ),
                                ft.Text(
                                    "2. Copie seu SteamID64 (17 números) nos detalhes da sua conta Steam.",
                                    size=11,
                                    color="#D1D5DB",
                                ),
                                ft.Text(
                                    "3. Deixe 'Detalhes dos jogos' como Público na privacidade do perfil.",
                                    size=11,
                                    color="#D1D5DB",
                                ),
                                ft.Row(
                                    spacing=8,
                                    controls=[
                                        ft.TextButton(
                                            "Abrir página da API Key",
                                            icon=ft.Icons.OPEN_IN_NEW,
                                            on_click=lambda _: webbrowser.open("https://steamcommunity.com/dev/apikey"),
                                        ),
                                        ft.TextButton(
                                            "Ver Detalhes da Conta",
                                            icon=ft.Icons.PERSON_SEARCH,
                                            on_click=lambda _: webbrowser.open("https://store.steampowered.com/account/"),
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ),
                    key_field,
                    id_field,
                    ft.Text(
                        f"Dados salvos localmente em: {DATA_DIR}",
                        size=10,
                        color="#6B7280",
                    ),
                ],
            ),
            actions=[
                ft.TextButton("Cancelar", on_click=lambda _: setattr(dlg, "open", False) or page.update()),
                ft.Button("Salvar e Sincronizar", on_click=save_and_close),
            ],
        )
        page.overlay.append(dlg)
        dlg.open = True
        page.update()

    def on_search_change(e):
        nonlocal search_query
        search_query = e.control.value
        render_games()

    def on_filter_change(e):
        nonlocal current_filter
        current_filter = e.control.value
        render_games()

    def stat_card(title, value_control):
        return ft.Container(
            bgcolor="#161B22",
            border=ft.Border.all(1, "#262C36"),
            border_radius=10,
            padding=ft.Padding.symmetric(horizontal=16, vertical=10),
            content=ft.Column(
                spacing=2,
                controls=[
                    ft.Text(title, size=11, color="#9BA3B0"),
                    value_control,
                ],
            ),
        )

    header = ft.Row(
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        controls=[
            ft.Row(
                spacing=14,
                controls=[
                    avatar_img,
                    ft.Column(
                        spacing=2,
                        controls=[profile_name, profile_sub],
                    ),
                ],
            ),
            ft.Row(
                spacing=10,
                controls=[
                    stat_card("Total de Jogos", stat_total_games),
                    stat_card("Horas Jogadas", stat_total_hours),
                    stat_card("Nunca Jogados", stat_unplayed),
                    stat_card("Instalados no PC", stat_installed),
                ],
            ),
        ],
    )

    btn_lib_view = ft.Button(
        "Biblioteca",
        icon=ft.Icons.GRID_VIEW,
        style=ft.ButtonStyle(bgcolor="#2563EB"),
        on_click=lambda _: switch_view("library"),
    )

    btn_stats_view = ft.Button(
        "Estatísticas",
        icon=ft.Icons.ANALYTICS,
        style=ft.ButtonStyle(bgcolor="#161B22"),
        on_click=lambda _: switch_view("stats"),
    )

    search_field = ft.TextField(
        hint_text="Buscar jogo na biblioteca...",
        prefix_icon=ft.Icons.SEARCH,
        width=220,
        height=42,
        text_size=13,
        on_change=on_search_change,
    )

    filter_dropdown = ft.Dropdown(
        width=195,
        value="all",
        text_size=13,
        options=[
            ft.dropdown.Option("all", "Todos (A-Z)"),
            ft.dropdown.Option("installed", "Instalados no PC"),
            ft.dropdown.Option("most_played", "Mais Jogados"),
            ft.dropdown.Option("played", "Já Jogados"),
            ft.dropdown.Option("unplayed", "Nunca Jogados"),
            ft.dropdown.Option("queue", "Backlog: Na Fila"),
            ft.dropdown.Option("playing", "Backlog: Jogando"),
            ft.dropdown.Option("completed", "Backlog: Zerados"),
            ft.dropdown.Option("dropped", "Backlog: Abandonados"),
        ],
        on_select=on_filter_change,
    )

    toolbar = ft.Row(
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        controls=[
            ft.Row(
                spacing=8,
                controls=[
                    btn_lib_view,
                    btn_stats_view,
                    search_field,
                    filter_dropdown,
                    ft.Button(
                        "O que jogar?",
                        icon=ft.Icons.CASINO,
                        on_click=open_roulette_dialog,
                    ),
                ],
            ),
            ft.Row(
                spacing=6,
                controls=[
                    loading_ring,
                    status_text,
                    ft.IconButton(
                        icon=ft.Icons.STORAGE,
                        tooltip="Reescanear discos locais (SSD/HD)",
                        on_click=rescan_disks_only,
                    ),
                    ft.Button(
                        "Sincronizar",
                        icon=ft.Icons.SYNC,
                        on_click=sync_steam_data,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.SETTINGS,
                        tooltip="Configurar API Key e SteamID",
                        on_click=open_config_dialog,
                    ),
                ],
            ),
        ],
    )

    page.add(
        header,
        ft.Divider(height=16, color="#262C36"),
        toolbar,
        ft.Container(height=6),
        games_grid,
        stats_view,
    )

    # Inicialização
    if cache.get("games"):
        update_stats_and_profile()
        render_games()
    elif not config.get("api_key") or not config.get("steam_id"):
        open_config_dialog()
    else:
        sync_steam_data()


if __name__ == "__main__":
    ft.run(main)