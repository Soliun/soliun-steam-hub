import flet as ft
import requests
import json
import os
import re
import random
import threading
import webbrowser

CONFIG_FILE = "config.json"
CACHE_FILE = "library_cache.json"
BACKLOG_FILE = "backlog.json"


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
    """Varre as pastas da Steam nos discos locais para identificar jogos instalados e tamanho."""
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
                        installed[appid] = size_bytes
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
    page.title = "Soliun Steam Hub"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = "#0E1117"
    page.window.width = 1220
    page.window.height = 800
    page.padding = 20

    config = load_json(CONFIG_FILE, {"api_key": "", "steam_id": ""})
    cache = load_json(CACHE_FILE, {"profile": {}, "games": []})
    backlog = load_json(BACKLOG_FILE, {})
    installed_map = scan_local_steam_games()

    current_filter = "all"
    search_query = ""

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
        max_extent=245,
        child_aspect_ratio=0.73,
        spacing=14,
        run_spacing=14,
    )

    def show_toast(message):
        snack = ft.SnackBar(
            bgcolor="#1F2937",
            duration=3000,
            content=ft.Row(
                spacing=10,
                controls=[
                    ft.Icon(ft.Icons.PLAY_CIRCLE_FILLED, color="#4ADE80", size=20),
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
        installed_bytes = sum(installed_map.get(g["appid"], 0) for g in games if g["appid"] in installed_map)

        stat_total_games.value = str(total_g)
        stat_total_hours.value = f"{int(total_m // 60):,}h".replace(",", ".")
        stat_unplayed.value = f"{unplayed_g} ({int((unplayed_g / total_g) * 100) if total_g else 0}%)"
        stat_installed.value = f"{installed_count} ({format_size_gb(installed_bytes)})"

    def set_game_status(appid, new_status):
        key = str(appid)
        if new_status == "none":
            backlog.pop(key, None)
        else:
            backlog[key] = new_status
        save_json(BACKLOG_FILE, backlog)
        if current_filter in ["queue", "playing", "completed", "dropped"]:
            render_games()
        else:
            page.update()

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
            filtered.sort(key=lambda x: installed_map.get(x["appid"], 0), reverse=True)
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
                tooltip="Clique para abrir/instalar na Steam",
                on_click=lambda e, aid=appid, gname=name: launch_steam_game(aid, gname),
                content=ft.Stack(
                    controls=[
                        ft.Image(
                            src=header_url,
                            width=225,
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
                                        format_size_gb(installed_map.get(appid, 0)),
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

            cards.append(
                ft.Container(
                    bgcolor="#161B22",
                    border_radius=10,
                    border=ft.Border.all(1, "#3B2D54" if is_inst else "#262C36"),
                    padding=10,
                    content=ft.Column(
                        spacing=8,
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
                                        padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                                        border_radius=6,
                                        content=ft.Text(
                                            format_hours(mins),
                                            size=11,
                                            color=badge_color,
                                            weight=ft.FontWeight.BOLD,
                                        ),
                                    ),
                                    ft.Text(f"ID: {appid}", size=10, color="#6B7280"),
                                ],
                            ),
                        ],
                    ),
                )
            )

        games_grid.controls = cards
        status_text.value = f"Exibindo {len(filtered)} de {len(games)} jogos"
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
                                f"SSD: {format_size_gb(installed_map[appid])}" if is_inst else "Não instalado no PC",
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
            title=ft.Text("Configurar Credenciais Steam"),
            content=ft.Column(
                tight=True,
                width=420,
                controls=[
                    ft.Text(
                        "Suas chaves ficam salvas apenas localmente no arquivo config.json.",
                        size=12,
                        color="#9BA3B0",
                    ),
                    key_field,
                    id_field,
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

    toolbar = ft.Row(
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        controls=[
            ft.Row(
                spacing=10,
                controls=[
                    ft.TextField(
                        hint_text="Buscar jogo na biblioteca...",
                        prefix_icon=ft.Icons.SEARCH,
                        width=270,
                        height=42,
                        text_size=13,
                        on_change=on_search_change,
                    ),
                    ft.Dropdown(
                        width=210,
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
                    ),
                    ft.Button(
                        "O que jogar?",
                        icon=ft.Icons.CASINO,
                        on_click=open_roulette_dialog,
                    ),
                ],
            ),
            ft.Row(
                spacing=10,
                controls=[
                    loading_ring,
                    status_text,
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