"""
Lecteur vidéo avec upmix surround de type Dolby Pro Logic II (approximation)

Prérequis :
  1. pip install python-mpv
  2. Télécharger libmpv (mpv-2.dll / libmpv-2.dll) depuis https://mpv.io/installation/
     (builds Windows) et la placer dans le même dossier que ce script.
  3. Windows : Panneau de configuration > Son > Configurer les haut-parleurs > 5.1
     (ou format 5.1 sur la sortie HDMI)

Lancer :  python lecteur_surround.py [video]

Modes audio :
  - Stéréo normale        : aucun traitement
  - Surround actif        : upmixer actif FFmpeg (filtre "surround"), direction adaptative
  - Matrice type PLII     : matrice passive Lt/Rt -> 5.0 avec surround retardés (10 ms)
                            et filtrés (passe-bas 7 kHz), comme un décodeur Pro Logic
Si la source est déjà multicanal (Dolby Digital 5.1...), elle est jouée telle quelle.
"""
import os
import sys
import tkinter as tk
from tkinter import filedialog, ttk

# Version compilée (PyInstaller) : indiquer où se trouve la DLL libmpv
if getattr(sys, "frozen", False):
    _base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    os.environ["PATH"] = _base + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(_base)

import mpv

MODES = {
    "Stéréo normale": None,
    "Surround actif (FFmpeg)": "lavfi=[surround=chl_out=5.1:lfe=0:angle=90]",
    "Matrice type PLII": (
        "lavfi=[pan=5.1|FL=FL|FR=FR|FC=0.707*FL+0.707*FR|LFE=0*FL"
        "|SL=0.707*FL-0.707*FR|SR=0.707*FR-0.707*FL,"
        "adelay=0|0|0|0|10|10,"
        "lowpass=f=7000:channels=SL+SR]"
    ),
}


def fmt(s):
    if not s or s < 0:
        s = 0
    s = int(s)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class Lecteur(tk.Tk):
    def __init__(self, fichier=None):
        super().__init__()
        self.title("Lecteur surround")
        self.geometry("980x620")
        self.configure(bg="black")
        self.glisse = False
        self.plein_ecran = False
        self.canaux_source = 0
        self._params = None        # renseigné par le thread mpv
        self._appliques = None     # (mode, source multicanal) déjà appliqués

        self._ui()

        self.mpv = mpv.MPV(
            wid=str(self.cadre.winfo_id()),
            input_default_bindings=False,
            input_vo_keyboard=False,
            osc=False,
            audio_normalize_downmix=False,
        )
        self.mpv.volume = 80

        @self.mpv.property_observer("audio-params")
        def _obs(_name, valeur):
            self._params = valeur

        self.bind("<space>", lambda e: self.pause())
        self.bind("<f>", lambda e: self.plein())
        self.bind("<Escape>", lambda e: self.plein_ecran and self.plein())
        self.bind("<Left>", lambda e: self.mpv.seek(-10))
        self.bind("<Right>", lambda e: self.mpv.seek(10))
        self.protocol("WM_DELETE_WINDOW", self.fermer)
        self.after(250, self._boucle)
        if fichier and os.path.isfile(fichier):
            self.after(300, lambda: self.ouvrir(fichier))

    # ---------- Interface ----------
    def _ui(self):
        self.cadre = tk.Frame(self, bg="black")
        self.cadre.pack(fill=tk.BOTH, expand=True)
        self.cadre.bind("<Double-Button-1>", lambda e: self.plein())

        self.ctrl = tk.Frame(self, bg="#222")
        self.ctrl.pack(fill=tk.X)

        l1 = tk.Frame(self.ctrl, bg="#222")
        l1.pack(fill=tk.X, padx=8, pady=(6, 0))
        self.t_cur = tk.Label(l1, text="00:00", fg="white", bg="#222", width=8)
        self.t_cur.pack(side=tk.LEFT)
        self.barre = ttk.Scale(l1, from_=0, to=1000, orient=tk.HORIZONTAL)
        self.barre.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self.barre.bind("<ButtonPress-1>", lambda e: setattr(self, "glisse", True))
        self.barre.bind("<ButtonRelease-1>", self._fin_glisse)
        self.t_dur = tk.Label(l1, text="00:00", fg="white", bg="#222", width=8)
        self.t_dur.pack(side=tk.LEFT)

        l2 = tk.Frame(self.ctrl, bg="#222")
        l2.pack(fill=tk.X, padx=8, pady=6)
        for txt, cmd in [("📂 Ouvrir", self.choisir), ("▶/⏸", self.pause),
                         ("⏪ 10s", lambda: self.mpv.seek(-10)),
                         ("10s ⏩", lambda: self.mpv.seek(10))]:
            ttk.Button(l2, text=txt, command=cmd).pack(side=tk.LEFT, padx=2)

        ttk.Button(l2, text="⛶", command=self.plein, width=3).pack(side=tk.RIGHT, padx=2)
        self.vol = ttk.Scale(l2, from_=0, to=100, length=110, orient=tk.HORIZONTAL,
                             command=lambda v: setattr(self.mpv, "volume", float(v)))
        self.vol.set(80)
        self.vol.pack(side=tk.RIGHT, padx=6)
        tk.Label(l2, text="🔊", fg="white", bg="#222").pack(side=tk.RIGHT)

        self.mode = tk.StringVar(value="Surround actif (FFmpeg)")
        ttk.Combobox(l2, textvariable=self.mode, values=list(MODES), state="readonly",
                     width=24).pack(side=tk.RIGHT, padx=10)
        self.info = tk.Label(l2, text="", fg="#8fb4ff", bg="#222")
        self.info.pack(side=tk.RIGHT)

    # ---------- Actions ----------
    def choisir(self):
        p = filedialog.askopenfilename(
            filetypes=[("Vidéos", "*.mp4 *.mkv *.avi *.mov *.wmv *.webm *.m4v *.ts *.mpg"),
                       ("Tous", "*.*")])
        if p:
            self.ouvrir(p)

    def ouvrir(self, chemin):
        self._appliques = None
        self.mpv.play(chemin)
        self.title(os.path.basename(chemin))

    def pause(self):
        self.mpv.pause = not self.mpv.pause

    def plein(self):
        self.plein_ecran = not self.plein_ecran
        self.attributes("-fullscreen", self.plein_ecran)
        if self.plein_ecran:
            self.ctrl.pack_forget()
        else:
            self.ctrl.pack(fill=tk.X)

    def _fin_glisse(self, _e):
        self.glisse = False
        self.mpv.seek(self.barre.get() / 10, reference="absolute-percent")

    def fermer(self):
        try:
            self.mpv.terminate()
        finally:
            self.destroy()

    # ---------- Traitement audio ----------
    def _appliquer_audio(self):
        multi = self.canaux_source > 2
        mode = self.mode.get()
        if self._appliques == (mode, multi):
            return
        self._appliques = (mode, multi)
        graphe = MODES[mode]
        try:
            if graphe is None or multi:
                self.mpv.command("af", "clr", "")
                self.mpv.audio_channels = "auto-safe"
            else:
                self.mpv.audio_channels = "5.1"
                self.mpv.command("af", "set", graphe)
        except Exception as e:
            self.info.config(text=f"Erreur filtre : {e}")
            return
        if multi:
            self.info.config(text=f"Source {self.canaux_source} canaux : lecture directe")
        elif graphe:
            self.info.config(text="Upmix 5.1 actif")
        else:
            self.info.config(text="Stéréo")

    def _boucle(self):
        p = self._params
        if p and p.get("channel-count") != self.canaux_source:
            self.canaux_source = p.get("channel-count") or 0
        if p:
            self._appliquer_audio()
        try:
            pos, dur = self.mpv.time_pos, self.mpv.duration
        except Exception:
            pos = dur = None
        if pos is not None and dur and not self.glisse:
            self.barre.set(pos / dur * 1000)
            self.t_cur.config(text=fmt(pos))
            self.t_dur.config(text=fmt(dur))
        self.after(250, self._boucle)


if __name__ == "__main__":
    Lecteur(sys.argv[1] if len(sys.argv) > 1 else None).mainloop()
