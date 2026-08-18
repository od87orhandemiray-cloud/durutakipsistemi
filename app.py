import streamlit as st
import pandas as pd
import datetime
import gspread
from io import BytesIO
from google.oauth2.service_account import Credentials
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ----------------------------------------------------------------------------------
# AYARLAR
# ----------------------------------------------------------------------------------
BIRIMLER = ["Retler", "Teknik Birim", "Satış Ekibi"]
UYELER_SHEET = "Uyeler"
KAYITLAR_SHEET = "Kayitlar"

st.set_page_config(page_title="Duru Takip Sistemi", layout="wide")

# ----------------------------------------------------------------------------------
# GOOGLE SHEETS BAĞLANTISI
# ----------------------------------------------------------------------------------
@st.cache_resource
def get_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]), scopes=scopes
    )
    return gspread.authorize(creds)


@st.cache_resource
def get_spreadsheet():
    client = get_client()
    return client.open_by_key(st.secrets["sheet_id"])


def get_or_create_worksheet(name, headers):
    ss = get_spreadsheet()
    try:
        ws = ss.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        ws = ss.add_worksheet(title=name, rows=1000, cols=len(headers) + 2)
        ws.append_row(headers)
    return ws


@st.cache_resource
def get_worksheet_cached(name, headers_tuple):
    return get_or_create_worksheet(name, list(headers_tuple))


@st.cache_data(ttl=20, show_spinner=False)
def load_df(sheet_name, headers_tuple):
    ws = get_worksheet_cached(sheet_name, headers_tuple)
    data = ws.get_all_records()
    if not data:
        return pd.DataFrame(columns=list(headers_tuple))
    df = pd.DataFrame(data)
    # E-tabloda henuz olmayan (sonradan eklenen) sutunlar icin bos deger ile tamamla,
    # boylece sema guncellemeleri eski verilerle KeyError vermeden calisir.
    for c in headers_tuple:
        if c not in df.columns:
            df[c] = ""
    return df


def overwrite_sheet(sheet_name, headers, df):
    ws = get_worksheet_cached(sheet_name, tuple(headers))
    ws.clear()
    ws.append_row(headers)
    if not df.empty:
        ws.append_rows(df[headers].astype(str).values.tolist())
    st.cache_data.clear()


# ----------------------------------------------------------------------------------
# VERI KATMANI
# ----------------------------------------------------------------------------------
UYELER_HEADERS = ["Ad Soyad", "Dahili", "Birim"]
KAYITLAR_HEADERS = [
    "Tarih", "Ad Soyad", "Dahili",
    "Arama Sayisi", "Arama Suresi Dk", "Arama Suresi Sn", "Depozit", "Dep Adet",
    "Ek Sure Dk", "Zoiper Sure Dk", "Toplam Dk", "Izin Durumu",
]
IZIN_SECENEKLERI = ["Yok", "Tam Gün İzinli", "Yarım Gün İzinli"]
SAYISAL_KOLONLAR = ["Arama Sayisi", "Arama Suresi Dk", "Arama Suresi Sn", "Depozit", "Dep Adet", "Ek Sure Dk", "Zoiper Sure Dk", "Toplam Dk"]


def load_uyeler():
    df = load_df(UYELER_SHEET, tuple(UYELER_HEADERS))
    if df.empty:
        return df
    df["Dahili"] = df["Dahili"].astype(str)
    return df


def save_uyeler(df):
    overwrite_sheet(UYELER_SHEET, UYELER_HEADERS, df)


def load_kayitlar():
    df = load_df(KAYITLAR_SHEET, tuple(KAYITLAR_HEADERS))
    if df.empty:
        return df
    for c in SAYISAL_KOLONLAR:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
    df["Dahili"] = df["Dahili"].astype(str)
    return df


def bulk_upsert_kayitlar(tarih, kayit_listesi):
    """Birden fazla kişinin o günkü kaydını TEK bir okuma + TEK bir yazma isteğiyle günceller.
    Her kayıt sözlüğü, ilgili birime göre yalnızca kullandığı alanları doldurur; diğerleri 0 kalır."""
    df = load_kayitlar()
    yeni_satirlar = []
    for k in kayit_listesi:
        # Arama süresi artık saniye hassasiyetiyle tutulur (h:mm:ss). "Arama Suresi Dk"
        # sadece Toplam Dk / Ortalama Rapor gibi dakika bazlı toplamlar için türetilir
        # (saniye kalırsa yukarı yuvarlanır); ekranda gösterilen ve sıralamada kullanılan
        # asıl değer "Arama Suresi Sn"dir.
        arama_saniye = int(k.get("Arama Suresi Sn", 0))
        arama_suresi_dk = arama_saniye // 60 + (1 if arama_saniye % 60 else 0)
        ek = int(k.get("Ek Sure Dk", 0))
        zoiper = int(k.get("Zoiper Sure Dk", 0))
        izin = k.get("Izin Durumu", "Yok")
        toplam = 0 if izin == "Tam Gün İzinli" else (arama_suresi_dk + ek + zoiper)
        yeni_satirlar.append({
            "Tarih": tarih, "Ad Soyad": k["Ad Soyad"], "Dahili": str(k.get("Dahili", "")),
            "Arama Sayisi": int(k.get("Arama Sayisi", 0)),
            "Arama Suresi Dk": arama_suresi_dk,
            "Arama Suresi Sn": arama_saniye,
            "Depozit": int(k.get("Depozit", 0)),
            "Dep Adet": int(k.get("Dep Adet", 0)),
            "Ek Sure Dk": ek, "Zoiper Sure Dk": zoiper,
            "Toplam Dk": int(toplam),
            "Izin Durumu": izin,
        })
    isimler = [s["Ad Soyad"] for s in yeni_satirlar]
    if not df.empty:
        mask = (df["Tarih"] == tarih) & (df["Ad Soyad"].isin(isimler))
        df = df[~mask]
    df = pd.concat([df, pd.DataFrame(yeni_satirlar)], ignore_index=True)
    overwrite_sheet(KAYITLAR_SHEET, KAYITLAR_HEADERS, df)


# ----------------------------------------------------------------------------------
# YARDIMCI: SAAT:DK:SN <-> SANIYE (ARAMA SURESI ARTIK SN HASSASIYETIYLE TUTULUR)
# ----------------------------------------------------------------------------------
def hhmmss_to_saniye(s):
    """'2:13:26' / '03:15:51' gibi bir metni toplam saniyeye çevirir."""
    try:
        parts = [int(p) for p in str(s).strip().split(":")]
        while len(parts) < 3:
            parts.insert(0, 0)
        h, m, sec = parts
        return h * 3600 + m * 60 + sec
    except Exception:
        return None


def saniye_to_hhmmss(sn):
    """Toplam saniyeyi 'H:MM:SS' metnine çevirir (görüntülemede kullanılır, dakikaya yuvarlamaz)."""
    try:
        sn = int(sn)
    except Exception:
        sn = 0
    if sn < 0:
        sn = 0
    h, kalan = divmod(sn, 3600)
    m, s = divmod(kalan, 60)
    return f"{h}:{m:02d}:{s:02d}"


def hhmmss_to_dk(s):
    """Geriye dönük uyumluluk için: h:mm:ss -> dakika (saniye kalırsa yukarı yuvarlar)."""
    sn = hhmmss_to_saniye(s)
    if sn is None:
        return None
    return sn // 60 + (1 if sn % 60 else 0)


def parse_block(text, deger_kolonu):
    rows = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        # ilk eleman site (atlanacak), ikinci deger, ucuncu dahili, gerisi ad soyad
        deger = parts[1]
        dahili = parts[2]
        ad_soyad = " ".join(parts[3:]).title()
        rows.append({"Dahili": dahili, "Ad Soyad": ad_soyad, deger_kolonu: deger})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------------
# YARDIMCI: SATIS EKIBI RENKLENDIRME (SADECE EN YUKSEK / EN DUSUK ARAMA SURESI)
# ----------------------------------------------------------------------------------
def satis_hesapla_minmax(df):
    aktif = df[df["Izin Durumu"] == "Yok"]
    if aktif.empty:
        return None, None
    sn = aktif["Arama Süresi"].apply(hhmmss_to_saniye)
    return sn.min(), sn.max()


def satis_rengi(row, min_val, max_val):
    if row["Izin Durumu"] == "Tam Gün İzinli":
        return "#7FDBFF"
    if row["Izin Durumu"] == "Yarım Gün İzinli":
        return "#D8B4FE"
    if min_val is not None and max_val is not None and min_val != max_val:
        sn = hhmmss_to_saniye(row["Arama Süresi"])
        if sn == max_val:
            return "#90EE90"
        if sn == min_val:
            return "#FF6B6B"
    return "#FFFFFF"


def satis_notu(row, min_val, max_val, emoji=True):
    if row["Izin Durumu"] != "Yok" or min_val is None or max_val is None or min_val == max_val:
        return ""
    sn = hhmmss_to_saniye(row["Arama Süresi"])
    if sn == max_val:
        return ("🎉 " if emoji else "★ ") + "EN YÜKSEK ARAMA SÜRESİ"
    if sn == min_val:
        return ("🚨 " if emoji else "⚠ ") + "EN DÜŞÜK ARAMA SÜRESİ"
    return ""


def satis_style(df, min_val, max_val):
    def row_style(row):
        return [f"background-color: {satis_rengi(row, min_val, max_val)}"] * len(row)
    return df.style.apply(row_style, axis=1)


# ----------------------------------------------------------------------------------
# YARDIMCI: RETLER RENKLENDIRME (EN YUKSEK DEPOZIT = YESIL, EN YUKSEK SURE = SADECE IBARE)
# ----------------------------------------------------------------------------------
def retler_hesapla_minmax(df):
    aktif = df[df["Izin Durumu"] == "Yok"]
    if aktif.empty:
        return None, None, None, None
    sn = aktif["Arama Süresi"].apply(hhmmss_to_saniye)
    return aktif["Depozit"].min(), aktif["Depozit"].max(), sn.min(), sn.max()


def retler_rengi(row, dep_min, dep_max):
    if row["Izin Durumu"] == "Tam Gün İzinli":
        return "#7FDBFF"
    if row["Izin Durumu"] == "Yarım Gün İzinli":
        return "#D8B4FE"
    if dep_min is not None and dep_max is not None and dep_min != dep_max and row["Depozit"] == dep_max:
        return "#90EE90"
    return "#FFFFFF"


def retler_notu(row, dep_min, dep_max, sure_min, sure_max, emoji=True):
    if row["Izin Durumu"] != "Yok":
        return ""
    parcalar = []
    if dep_min is not None and dep_max is not None and dep_min != dep_max and row["Depozit"] == dep_max:
        parcalar.append(("💰 " if emoji else "$ ") + "EN YÜKSEK DEPOZİT")
    if sure_min is not None and sure_max is not None and sure_min != sure_max and hhmmss_to_saniye(row["Arama Süresi"]) == sure_max:
        parcalar.append(("⏱️ " if emoji else "* ") + "EN YÜKSEK ARAMA SÜRESİ")
    return " | ".join(parcalar)


def retler_style(df, dep_min, dep_max):
    def row_style(row):
        return [f"background-color: {retler_rengi(row, dep_min, dep_max)}"] * len(row)
    return df.style.apply(row_style, axis=1)


# ----------------------------------------------------------------------------------
# YARDIMCI: TEKNIK BIRIM (RENKSIZ, ESKI DUZEN)
# ----------------------------------------------------------------------------------
def teknik_style(df):
    def row_style(row):
        if row["Izin Durumu"] == "Tam Gün İzinli":
            renk = "#7FDBFF"
        elif row["Izin Durumu"] == "Yarım Gün İzinli":
            renk = "#D8B4FE"
        else:
            renk = "#FFFFFF"
        return [f"background-color: {renk}"] * len(row)
    return df.style.apply(row_style, axis=1)


# ----------------------------------------------------------------------------------
# YARDIMCI: PNG GORSEL OLUSTURMA (GENEL AMACLI)
# ----------------------------------------------------------------------------------
def build_image(baslik, columns, col_widths, satirlar):
    """satirlar: [(hucre_metinleri_listesi, hucre_renkleri_listesi, kalin_mi), ...]"""
    cell_text = [columns] + [s[0] for s in satirlar]
    cell_colors = [["#E5E7EB"] * len(columns)] + [s[1] for s in satirlar]
    kalin_satirlar = {0}

    fig_height = max(2, len(cell_text) * 0.4 + 0.7)
    fig, ax = plt.subplots(figsize=(11, fig_height))
    ax.axis("off")
    ax.set_title(baslik, fontsize=15, fontweight="bold", loc="left")
    tbl = ax.table(cellText=cell_text, cellColours=cell_colors, loc="center", cellLoc="left", colWidths=col_widths)
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.scale(1, 1.7)
    for (r, c), cell in tbl.get_celld().items():
        cell.set_edgecolor("#D1D5DB")
        cell.set_text_props(weight="bold" if r in kalin_satirlar else "normal", color="#111827")

    buf = BytesIO()
    plt.savefig(buf, format="png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def build_satis_image(tarih_str, df, min_val, max_val):
    columns = ["İsim Soyisim", "Dahili", "Arama Sayısı", "Arama Süresi", "İzin Durumu", "Not"]
    satirlar = []
    for _, r in df.iterrows():
        renk = satis_rengi(r, min_val, max_val)
        not_metni = satis_notu(r, min_val, max_val, emoji=False)
        metinler = [str(r["İsim Soyisim"]), str(r["Dahili"]), str(r["Arama Sayısı"]), str(r["Arama Süresi"]), str(r["Izin Durumu"]), not_metni]
        satirlar.append((metinler, [renk] * len(columns), False))
    return build_image(f"{tarih_str} — Satış Ekibi", columns, [0.20, 0.10, 0.14, 0.16, 0.15, 0.25], satirlar)


def build_retler_image(tarih_str, df, dep_min, dep_max, sure_min, sure_max):
    columns = ["İsim Soyisim", "Dahili", "Arama Sayısı", "Arama Süresi", "Depozit", "Dep Adet", "İzin Durumu", "Not"]
    satirlar = []
    for _, r in df.iterrows():
        renk = retler_rengi(r, dep_min, dep_max)
        not_metni = retler_notu(r, dep_min, dep_max, sure_min, sure_max, emoji=False)
        metinler = [
            str(r["İsim Soyisim"]), str(r["Dahili"]), str(r["Arama Sayısı"]), str(r["Arama Süresi"]),
            str(r["Depozit"]), str(r["Dep Adet"]), str(r["Izin Durumu"]), not_metni,
        ]
        satirlar.append((metinler, [renk] * len(columns), False))
    return build_image(f"{tarih_str} — Retler", columns, [0.17, 0.09, 0.11, 0.13, 0.11, 0.11, 0.13, 0.25], satirlar)


# ----------------------------------------------------------------------------------
# YARDIMCI: ORTALAMA RAPOR (TUM GUNLER DAHIL, SADECE TAM GUN IZIN HARIC)
# ----------------------------------------------------------------------------------
def ortalama_rapor(kayitlar_df, uyeler_df, baslangic, bitis):
    if kayitlar_df.empty or uyeler_df.empty:
        return pd.DataFrame()

    df = kayitlar_df.copy()
    df["Tarih_dt"] = pd.to_datetime(df["Tarih"], errors="coerce")
    df = df[(df["Tarih_dt"].dt.date >= baslangic) & (df["Tarih_dt"].dt.date <= bitis)]
    df = df[df["Izin Durumu"] != "Tam Gün İzinli"]  # tam gun izinli gunleri tamamen cikar

    if df.empty:
        return pd.DataFrame()

    ozet = df.groupby("Ad Soyad").agg(
        Gun_Sayisi=("Tarih", "nunique"),
        Toplam_Arama=("Arama Sayisi", "sum"),
        Toplam_Arama_Suresi=("Arama Suresi Dk", "sum"),
        Toplam_Depozit=("Depozit", "sum"),
        Toplam_Dep_Adet=("Dep Adet", "sum"),
        Toplam_Ek_Sure=("Ek Sure Dk", "sum"),
        Toplam_Zoiper_Sure=("Zoiper Sure Dk", "sum"),
        Toplam_Dk=("Toplam Dk", "sum"),
    ).reset_index()

    ozet["Ort. Arama Sayısı"] = (ozet["Toplam_Arama"] / ozet["Gun_Sayisi"]).round(1)
    ozet["Ort. Arama Süresi (dk)"] = (ozet["Toplam_Arama_Suresi"] / ozet["Gun_Sayisi"]).round(1)
    ozet["Ort. Depozit"] = (ozet["Toplam_Depozit"] / ozet["Gun_Sayisi"]).round(1)
    ozet["Ort. Dep Adet"] = (ozet["Toplam_Dep_Adet"] / ozet["Gun_Sayisi"]).round(1)
    ozet["Ort. Toplam Dk"] = (ozet["Toplam_Dk"] / ozet["Gun_Sayisi"]).round(1)

    ozet = ozet.merge(uyeler_df[["Ad Soyad", "Birim"]], on="Ad Soyad", how="left")

    return ozet[["Ad Soyad", "Birim", "Gun_Sayisi", "Ort. Arama Sayısı", "Ort. Arama Süresi (dk)", "Ort. Depozit", "Ort. Dep Adet", "Ort. Toplam Dk"]].rename(
        columns={"Gun_Sayisi": "Hesaba Katılan Gün Sayısı"}
    )


# ----------------------------------------------------------------------------------
# OTURUM DURUMU
# ----------------------------------------------------------------------------------
if "admin" not in st.session_state:
    st.session_state.admin = False

st.title("📞 Duru Takip Sistemi")

with st.sidebar:
    st.header("Yönetici Girişi")
    if not st.session_state.admin:
        pw = st.text_input("Şifre", type="password")
        if st.button("Giriş Yap"):
            if pw == st.secrets.get("admin_password", ""):
                st.session_state.admin = True
                st.rerun()
            else:
                st.error("Şifre hatalı.")
    else:
        st.success("Yönetici olarak giriş yapıldı.")
        if st.button("Çıkış Yap"):
            st.session_state.admin = False
            st.rerun()

secim_tarih = st.date_input("Tarih Seç", value=datetime.date.today() - datetime.timedelta(days=1))
tarih_str = secim_tarih.strftime("%Y-%m-%d")

uyeler = load_uyeler()
kayitlar = load_kayitlar()

# ----------------------------------------------------------------------------------
# YONETICI PANELI
# ----------------------------------------------------------------------------------
if st.session_state.admin:
    tab1, tab2, tab3 = st.tabs(["📋 Günlük Veri Girişi", "👥 Birim Yönetimi", "🧩 Ayrıştırma (Otomatik Doldur)"])

    # ---------------- TAB 1: GUNLUK VERI GIRISI (BIRIME OZEL ALANLAR) ----------------
    with tab1:
        st.subheader(f"{tarih_str} için Görüşme Verisi Girişi")
        if uyeler.empty:
            st.info("Henüz üye eklenmedi. Önce 'Birim Yönetimi' sekmesinden üye ekleyin ya da Ayrıştırma aracını kullanın.")
        else:
            with st.form("gunluk_giris_formu"):
                girisler = []
                for birim in BIRIMLER + ["Atanmamış"]:
                    grup = uyeler[uyeler["Birim"] == birim] if birim != "Atanmamış" else uyeler[~uyeler["Birim"].isin(BIRIMLER)]
                    if grup.empty:
                        continue
                    st.markdown(f"### {birim}")
                    for _, kisi in grup.iterrows():
                        ad = kisi["Ad Soyad"]
                        dahili = kisi["Dahili"]
                        mevcut = kayitlar[(kayitlar["Tarih"] == tarih_str) & (kayitlar["Ad Soyad"] == ad)]
                        m = mevcut.iloc[0] if not mevcut.empty else None
                        st.markdown(f"**{ad}**  (Dahili: {dahili})")
                        mevcut_izin = m["Izin Durumu"] if m is not None and "Izin Durumu" in m else "Yok"
                        izin_idx = IZIN_SECENEKLERI.index(mevcut_izin) if mevcut_izin in IZIN_SECENEKLERI else 0

                        # Arama süresini h:mm:ss olarak saklıyoruz; eski kayıtlarda "Arama Suresi Sn"
                        # yoksa "Arama Suresi Dk"dan (saniyesiz) türetilir.
                        if m is not None and "Arama Suresi Sn" in m and pd.notna(m["Arama Suresi Sn"]) and int(m["Arama Suresi Sn"]) > 0:
                            mevcut_sn = int(m["Arama Suresi Sn"])
                        elif m is not None:
                            mevcut_sn = int(m["Arama Suresi Dk"]) * 60
                        else:
                            mevcut_sn = 0

                        if birim == "Retler":
                            c0, c1, c2, c3, c4, c5, c6 = st.columns([1.1, 0.9, 0.7, 0.7, 0.7, 0.9, 0.9])
                            izin_durumu = c0.selectbox("İzin Durumu", IZIN_SECENEKLERI, index=izin_idx, key=f"izin_{ad}_{tarih_str}")
                            arama = c1.number_input("Arama Sayısı", min_value=0, value=int(m["Arama Sayisi"]) if m is not None else 0, key=f"arama_{ad}_{tarih_str}")
                            sure_saat = c2.number_input("Süre Saat", min_value=0, value=mevcut_sn // 3600, key=f"suresaat_{ad}_{tarih_str}")
                            sure_dk = c3.number_input("Süre Dk", min_value=0, max_value=59, value=(mevcut_sn % 3600) // 60, key=f"suredk_{ad}_{tarih_str}")
                            sure_sn = c4.number_input("Süre Sn", min_value=0, max_value=59, value=mevcut_sn % 60, key=f"suresn_{ad}_{tarih_str}")
                            depozit = c5.number_input("Depozit", min_value=0, value=int(m["Depozit"]) if m is not None else 0, key=f"depozit_{ad}_{tarih_str}")
                            dep_adet = c6.number_input("Dep Adet", min_value=0, value=int(m["Dep Adet"]) if m is not None else 0, key=f"depadet_{ad}_{tarih_str}")
                            st.markdown("---")
                            girisler.append({
                                "Ad Soyad": ad, "Dahili": dahili, "Arama Sayisi": arama,
                                "Arama Suresi Sn": sure_saat * 3600 + sure_dk * 60 + sure_sn,
                                "Depozit": depozit, "Dep Adet": dep_adet,
                                "Izin Durumu": izin_durumu,
                            })

                        elif birim == "Satış Ekibi":
                            c0, c1, c2, c3, c4 = st.columns([1.2, 1, 0.8, 0.8, 0.8])
                            izin_durumu = c0.selectbox("İzin Durumu", IZIN_SECENEKLERI, index=izin_idx, key=f"izin_{ad}_{tarih_str}")
                            arama = c1.number_input("Arama Sayısı", min_value=0, value=int(m["Arama Sayisi"]) if m is not None else 0, key=f"arama_{ad}_{tarih_str}")
                            sure_saat = c2.number_input("Süre Saat", min_value=0, value=mevcut_sn // 3600, key=f"suresaat_{ad}_{tarih_str}")
                            sure_dk = c3.number_input("Süre Dk", min_value=0, max_value=59, value=(mevcut_sn % 3600) // 60, key=f"suredk_{ad}_{tarih_str}")
                            sure_sn = c4.number_input("Süre Sn", min_value=0, max_value=59, value=mevcut_sn % 60, key=f"suresn_{ad}_{tarih_str}")
                            st.markdown("---")
                            girisler.append({
                                "Ad Soyad": ad, "Dahili": dahili, "Arama Sayisi": arama,
                                "Arama Suresi Sn": sure_saat * 3600 + sure_dk * 60 + sure_sn,
                                "Izin Durumu": izin_durumu,
                            })

                        else:  # Teknik Birim / Atanmamış -> eski duzen (zoiper + ek sure)
                            c0, c1, c2, c3, c4, c5 = st.columns([1.2, 1, 1, 1, 1, 1])
                            izin_durumu = c0.selectbox("İzin Durumu", IZIN_SECENEKLERI, index=izin_idx, key=f"izin_{ad}_{tarih_str}")
                            arama = c1.number_input("Arama Sayısı", min_value=0, value=int(m["Arama Sayisi"]) if m is not None else 0, key=f"arama_{ad}_{tarih_str}")
                            zoiper_saat = c2.number_input("Zoiper Saat", min_value=0, value=int(m["Zoiper Sure Dk"]) // 60 if m is not None else 0, key=f"zsaat_{ad}_{tarih_str}")
                            zoiper_dk_in = c3.number_input("Zoiper Dk", min_value=0, max_value=59, value=int(m["Zoiper Sure Dk"]) % 60 if m is not None else 0, key=f"zdk_{ad}_{tarih_str}")
                            ek_saat = c4.number_input("Ek Süre Saat", min_value=0, value=int(m["Ek Sure Dk"]) // 60 if m is not None else 0, key=f"esaat_{ad}_{tarih_str}")
                            ek_dk_in = c5.number_input("Ek Süre Dk", min_value=0, max_value=59, value=int(m["Ek Sure Dk"]) % 60 if m is not None else 0, key=f"edk_{ad}_{tarih_str}")
                            st.markdown("---")
                            girisler.append({
                                "Ad Soyad": ad, "Dahili": dahili, "Arama Sayisi": arama,
                                "Ek Sure Dk": ek_saat * 60 + ek_dk_in, "Zoiper Sure Dk": zoiper_saat * 60 + zoiper_dk_in,
                                "Izin Durumu": izin_durumu,
                            })

                gonder = st.form_submit_button("💾 Tümünü Kaydet", type="primary", use_container_width=True)
                if gonder:
                    bulk_upsert_kayitlar(tarih_str, girisler)
                    st.success(f"{len(girisler)} kişinin verisi tek seferde kaydedildi.")
                    st.rerun()

    # ---------------- TAB 2: BIRIM YONETIMI ----------------
    with tab2:
        st.subheader("Üye / Birim Yönetimi")
        st.caption("Aşağıdaki tabloyu doğrudan düzenleyebilirsiniz. Dahili numarası burada sabitlenir. Birim sütununa: " + ", ".join(BIRIMLER))
        duzenlenmis = st.data_editor(
            uyeler if not uyeler.empty else pd.DataFrame(columns=UYELER_HEADERS),
            num_rows="dynamic",
            column_config={
                "Birim": st.column_config.SelectboxColumn("Birim", options=BIRIMLER + ["Atanmamış"]),
            },
            use_container_width=True,
            key="uye_editor",
        )
        if st.button("Üye Listesini Kaydet"):
            save_uyeler(duzenlenmis)
            st.success("Üye listesi güncellendi.")
            st.rerun()

    # ---------------- TAB 3: AYRISTIRMA ----------------
    with tab3:
        st.subheader("Otomatik Ayrıştırma")
        st.caption("İki farklı listeyi (Adet ve Süre raporları) aşağıya yapıştırın. Sistem 'MERKEZ' ibaresini kaldırır ve Dahili numarasına göre eşleştirir. Süre, dakikaya çevrilmeden yapıştırıldığı gibi (saat:dk:sn) 'Süre' alanına yazılır; Retler için Depozit/Dep Adet'i ve birimi elle tamamlamanız gerekir.")
        c1, c2 = st.columns(2)
        with c1:
            blok_adet = st.text_area("Arama Sayısı Listesi (Site Adet Dahili Ad Soyad)", height=300, placeholder="MERKEZ 242 4004 DOGUKAN BASARAN")
        with c2:
            blok_sure = st.text_area("Süre Listesi (Site Süre Dahili Ad Soyad)", height=300, placeholder="MERKEZ 03:15:51 4014 Nur Guler")

        if st.button("Ayrıştır"):
            df_adet = parse_block(blok_adet, "Arama Sayısı")
            df_sure = parse_block(blok_sure, "Süre")
            merged = pd.merge(
                df_adet[["Dahili", "Ad Soyad", "Arama Sayısı"]] if not df_adet.empty else pd.DataFrame(columns=["Dahili", "Ad Soyad", "Arama Sayısı"]),
                df_sure[["Dahili", "Süre"]] if not df_sure.empty else pd.DataFrame(columns=["Dahili", "Süre"]),
                on="Dahili", how="outer",
            )
            if not df_sure.empty:
                ad_map = dict(zip(df_sure["Dahili"], df_sure["Ad Soyad"]))
                merged["Ad Soyad"] = merged.apply(lambda r: r["Ad Soyad"] if pd.notna(r["Ad Soyad"]) else ad_map.get(r["Dahili"], ""), axis=1)
            merged["Arama Sayısı"] = pd.to_numeric(merged["Arama Sayısı"], errors="coerce").fillna(0).astype(int)
            merged["Süre"] = merged["Süre"].fillna("0:00:00")
            # Sıralama/onay için saniyeye çevrilir, ama ekranda hep "Süre" (h:mm:ss) yazıldığı gibi kalır
            merged["_ArSureSn"] = merged["Süre"].apply(hhmmss_to_saniye).fillna(0).astype(int)
            merged = merged.sort_values("_ArSureSn", ascending=False).drop(columns=["_ArSureSn"]).reset_index(drop=True)
            merged["Depozit"] = 0
            merged["Dep Adet"] = 0
            merged["Izin Durumu"] = "Yok"
            merged["Birim"] = "Atanmamış"
            st.session_state.ayristirma_sonuc = merged
            st.session_state.ayristirma_tarih = tarih_str

        if "ayristirma_sonuc" in st.session_state:
            st.markdown("#### Onay Bekleyen Liste (düzenleyip onaylayabilirsiniz)")
            duzenlenmis2 = st.data_editor(
                st.session_state.ayristirma_sonuc,
                num_rows="dynamic",
                column_config={
                    "Birim": st.column_config.SelectboxColumn("Birim", options=BIRIMLER + ["Atanmamış"]),
                    "Izin Durumu": st.column_config.SelectboxColumn("Izin Durumu", options=IZIN_SECENEKLERI),
                },
                use_container_width=True,
                key="ayristirma_editor",
            )
            if st.button("Onayla ve Kaydet", type="primary"):
                u = load_uyeler()
                for _, r in duzenlenmis2.iterrows():
                    dahili = str(r["Dahili"])
                    ad = r["Ad Soyad"]
                    if u.empty or dahili not in u["Dahili"].values:
                        u = pd.concat([u, pd.DataFrame([{"Ad Soyad": ad, "Dahili": dahili, "Birim": r["Birim"]}])], ignore_index=True)
                    else:
                        u.loc[u["Dahili"] == dahili, "Birim"] = r["Birim"]
                save_uyeler(u)
                kayit_listesi = [
                    {
                        "Ad Soyad": r["Ad Soyad"], "Dahili": str(r["Dahili"]),
                        "Arama Sayisi": r["Arama Sayısı"],
                        "Arama Suresi Sn": hhmmss_to_saniye(r.get("Süre", "0:00:00")) or 0,
                        "Depozit": r.get("Depozit", 0), "Dep Adet": r.get("Dep Adet", 0),
                        "Izin Durumu": r.get("Izin Durumu", "Yok"),
                    }
                    for _, r in duzenlenmis2.iterrows()
                ]
                bulk_upsert_kayitlar(st.session_state.ayristirma_tarih, kayit_listesi)
                del st.session_state.ayristirma_sonuc
                st.success("Liste işlendi ve kaydedildi.")
                st.rerun()

# ----------------------------------------------------------------------------------
# HERKESE ACIK GORUNUM (SADECE O GUNUN TABLOSU) - HER BIRIM KENDI DUZENINDE
# ----------------------------------------------------------------------------------
st.markdown("---")
st.subheader(f"📊 {tarih_str} Tarihli Görüşme Tablosu")

if uyeler.empty:
    st.info("Henüz kayıtlı üye yok.")
else:
    gunun_kayitlari = kayitlar[kayitlar["Tarih"] == tarih_str] if not kayitlar.empty else pd.DataFrame(columns=KAYITLAR_HEADERS)

    def hazirla(birim):
        grup_uyeler = uyeler[uyeler["Birim"] == birim] if birim != "Atanmamış" else uyeler[~uyeler["Birim"].isin(BIRIMLER)]
        if grup_uyeler.empty:
            return None
        birlesik = grup_uyeler.merge(gunun_kayitlari, on="Ad Soyad", how="left", suffixes=("", "_k"))
        for c in SAYISAL_KOLONLAR:
            birlesik[c] = pd.to_numeric(birlesik[c], errors="coerce").fillna(0).astype(int)
        birlesik["Izin Durumu"] = birlesik["Izin Durumu"].fillna("Yok")
        birlesik["Dahili"] = birlesik["Dahili"].astype(str)
        return birlesik

    # ---------------- RETLER ----------------
    retler_df = hazirla("Retler")
    if retler_df is not None:
        gosterim = retler_df.rename(columns={"Ad Soyad": "İsim Soyisim", "Arama Sayisi": "Arama Sayısı"})
        gosterim["Arama Süresi"] = gosterim["Arama Suresi Sn"].apply(saniye_to_hhmmss)
        gosterim = gosterim[["İsim Soyisim", "Dahili", "Arama Sayısı", "Arama Süresi", "Depozit", "Dep Adet", "Izin Durumu"]]
        gosterim["_sn"] = gosterim["Arama Süresi"].apply(hhmmss_to_saniye)
        gosterim = gosterim.sort_values("_sn", ascending=False).drop(columns=["_sn"]).reset_index(drop=True)
        dep_min, dep_max, sure_min, sure_max = retler_hesapla_minmax(gosterim)
        gosterim["Not"] = gosterim.apply(lambda r: retler_notu(r, dep_min, dep_max, sure_min, sure_max, emoji=True), axis=1)
        st.markdown("#### Retler")
        st.dataframe(retler_style(gosterim, dep_min, dep_max), use_container_width=True, hide_index=True)
        st.caption("🟩 En yüksek depozit   ⏱️ En yüksek arama süresi (renksiz, sadece ibare)   🟦 Tam gün izinli   🟪 Yarım gün izinli")
        if st.button("🖼️ Retler Görseli Oluştur (PNG)"):
            st.session_state.retler_png = build_retler_image(tarih_str, gosterim, dep_min, dep_max, sure_min, sure_max)
            st.session_state.retler_png_tarih = tarih_str
        if st.session_state.get("retler_png") and st.session_state.get("retler_png_tarih") == tarih_str:
            st.image(st.session_state.retler_png, caption=f"{tarih_str} — Retler")
            st.download_button("📥 Retler PNG indir", data=st.session_state.retler_png, file_name=f"retler_{tarih_str}.png", mime="image/png", key="retler_indir")

    st.markdown("---")

    # ---------------- SATIŞ EKİBİ ----------------
    satis_df = hazirla("Satış Ekibi")
    if satis_df is not None:
        gosterim = satis_df.rename(columns={"Ad Soyad": "İsim Soyisim", "Arama Sayisi": "Arama Sayısı"})
        gosterim["Arama Süresi"] = gosterim["Arama Suresi Sn"].apply(saniye_to_hhmmss)
        gosterim = gosterim[["İsim Soyisim", "Dahili", "Arama Sayısı", "Arama Süresi", "Izin Durumu"]]
        gosterim["_sn"] = gosterim["Arama Süresi"].apply(hhmmss_to_saniye)
        gosterim = gosterim.sort_values("_sn", ascending=False).drop(columns=["_sn"]).reset_index(drop=True)
        min_val, max_val = satis_hesapla_minmax(gosterim)
        gosterim["Not"] = gosterim.apply(lambda r: satis_notu(r, min_val, max_val, emoji=True), axis=1)
        st.markdown("#### Satış Ekibi")
        st.dataframe(satis_style(gosterim, min_val, max_val), use_container_width=True, hide_index=True)
        st.caption("🟩 En yüksek arama süresi   🟥 En düşük arama süresi   🟦 Tam gün izinli   🟪 Yarım gün izinli")
        if st.button("🖼️ Satış Ekibi Görseli Oluştur (PNG)"):
            st.session_state.satis_png = build_satis_image(tarih_str, gosterim, min_val, max_val)
            st.session_state.satis_png_tarih = tarih_str
        if st.session_state.get("satis_png") and st.session_state.get("satis_png_tarih") == tarih_str:
            st.image(st.session_state.satis_png, caption=f"{tarih_str} — Satış Ekibi")
            st.download_button("📥 Satış Ekibi PNG indir", data=st.session_state.satis_png, file_name=f"satis_ekibi_{tarih_str}.png", mime="image/png", key="satis_indir")

    st.markdown("---")

    # ---------------- TEKNIK BIRIM (RENKSIZ, ESKI DUZEN) ----------------
    teknik_df = hazirla("Teknik Birim")
    if teknik_df is not None:
        gosterim = teknik_df.rename(columns={
            "Ad Soyad": "İsim Soyisim", "Arama Sayisi": "Arama Sayısı",
            "Ek Sure Dk": "Ek Süre (dk)", "Zoiper Sure Dk": "Zoiper Süre (dk)",
        })
        gosterim = gosterim[["İsim Soyisim", "Arama Sayısı", "Ek Süre (dk)", "Zoiper Süre (dk)", "Toplam Dk", "Izin Durumu"]]
        st.markdown("#### Teknik Birim")
        st.dataframe(teknik_style(gosterim), use_container_width=True, hide_index=True)
        st.caption("🟦 Tam gün izinli   🟪 Yarım gün izinli   (renk skalası uygulanmaz)")

    # ---------------- ATANMAMIŞ (varsa, Teknik Birim ile ayni eski duzen) ----------------
    atanmamis_df = hazirla("Atanmamış")
    if atanmamis_df is not None:
        gosterim = atanmamis_df.rename(columns={
            "Ad Soyad": "İsim Soyisim", "Arama Sayisi": "Arama Sayısı",
            "Ek Sure Dk": "Ek Süre (dk)", "Zoiper Sure Dk": "Zoiper Süre (dk)",
        })
        gosterim = gosterim[["İsim Soyisim", "Arama Sayısı", "Ek Süre (dk)", "Zoiper Süre (dk)", "Toplam Dk", "Izin Durumu"]]
        st.markdown("#### Atanmamış")
        st.dataframe(teknik_style(gosterim), use_container_width=True, hide_index=True)

# ----------------------------------------------------------------------------------
# ORTALAMA RAPOR (TARIH ARALIGI) - HAFTASONU VE TAM GUN IZIN HARIC
# ----------------------------------------------------------------------------------
st.markdown("---")
st.subheader("📈 Ortalama Rapor (Tarih Aralığı)")
st.caption("Seçilen tarih aralığındaki tüm günlerin (hafta sonu dahil) ortalaması alınır. Sadece kişinin tam gün izinli olduğu günler hesaba katılmaz. Yarım gün izinli günler, o günün gerçek verisiyle hesaba dahil edilir.")

rc1, rc2 = st.columns(2)
r_baslangic = rc1.date_input("Başlangıç Tarihi", value=datetime.date.today() - datetime.timedelta(days=7), key="rapor_baslangic")
r_bitis = rc2.date_input("Bitiş Tarihi", value=datetime.date.today() - datetime.timedelta(days=1), key="rapor_bitis")

if st.button("Ortalama Raporu Oluştur"):
    if r_baslangic > r_bitis:
        st.error("Başlangıç tarihi, bitiş tarihinden sonra olamaz.")
    else:
        rapor = ortalama_rapor(kayitlar, uyeler, r_baslangic, r_bitis)
        if rapor.empty:
            st.info("Seçilen aralıkta (tam gün izinler hariç) veri bulunamadı.")
        else:
            for birim in BIRIMLER + ["Atanmamış"]:
                grup = rapor[rapor["Birim"] == birim] if birim != "Atanmamış" else rapor[~rapor["Birim"].isin(BIRIMLER)]
                if grup.empty:
                    continue
                st.markdown(f"#### {birim}")
                st.dataframe(grup.drop(columns=["Birim"]), use_container_width=True, hide_index=True)
