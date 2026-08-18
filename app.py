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
RENK_SKALASI_UYGULANAN_BIRIMLER = ["Retler", "Satış Ekibi"]  # Teknik Birim'e renk skalası (en yüksek/en düşük/120dk) uygulanmaz
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
    return pd.DataFrame(data)


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
KAYITLAR_HEADERS = ["Tarih", "Ad Soyad", "Dahili", "Arama Sayisi", "Ek Sure Dk", "Zoiper Sure Dk", "Toplam Dk", "Izin Durumu"]
IZIN_SECENEKLERI = ["Yok", "Tam Gün İzinli", "Yarım Gün İzinli"]


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
    for c in ["Arama Sayisi", "Ek Sure Dk", "Zoiper Sure Dk", "Toplam Dk"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
    df["Dahili"] = df["Dahili"].astype(str)
    return df


def upsert_kayit(tarih, ad_soyad, dahili, arama, ek_dk, zoiper_dk, izin_durumu):
    bulk_upsert_kayitlar(tarih, [{
        "Ad Soyad": ad_soyad, "Dahili": dahili, "Arama Sayisi": arama,
        "Ek Sure Dk": ek_dk, "Zoiper Sure Dk": zoiper_dk, "Izin Durumu": izin_durumu,
    }])


def bulk_upsert_kayitlar(tarih, kayit_listesi):
    """Birden fazla kişinin o günkü kaydını TEK bir okuma + TEK bir yazma isteğiyle günceller."""
    df = load_kayitlar()
    yeni_satirlar = []
    for k in kayit_listesi:
        toplam = 0 if k["Izin Durumu"] == "Tam Gün İzinli" else (int(k["Ek Sure Dk"]) + int(k["Zoiper Sure Dk"]))
        yeni_satirlar.append({
            "Tarih": tarih, "Ad Soyad": k["Ad Soyad"], "Dahili": str(k["Dahili"]),
            "Arama Sayisi": int(k["Arama Sayisi"]), "Ek Sure Dk": int(k["Ek Sure Dk"]),
            "Zoiper Sure Dk": int(k["Zoiper Sure Dk"]), "Toplam Dk": int(toplam),
            "Izin Durumu": k["Izin Durumu"],
        })
    isimler = [s["Ad Soyad"] for s in yeni_satirlar]
    if not df.empty:
        mask = (df["Tarih"] == tarih) & (df["Ad Soyad"].isin(isimler))
        df = df[~mask]
    df = pd.concat([df, pd.DataFrame(yeni_satirlar)], ignore_index=True)
    overwrite_sheet(KAYITLAR_SHEET, KAYITLAR_HEADERS, df)


# ----------------------------------------------------------------------------------
# YARDIMCI: SAAT:DK:SN -> DK CEVIRME (SANIYE VARSA YUKARI YUVARLA)
# ----------------------------------------------------------------------------------
def hhmmss_to_dk(s):
    try:
        parts = [int(p) for p in s.strip().split(":")]
        while len(parts) < 3:
            parts.insert(0, 0)
        h, m, sec = parts
        total = h * 60 + m
        if sec > 0:
            total += 1
        return total
    except Exception:
        return None


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
# YARDIMCI: RENKLENDIRME VE ETIKETLEME
# ----------------------------------------------------------------------------------
def hesapla_minmax(df, apply_minmax):
    if not apply_minmax:
        return None, None
    aktif = df[df["Izin Durumu"] == "Yok"]
    if aktif.empty:
        return None, None
    return aktif["Toplam Dk"].min(), aktif["Toplam Dk"].max()


def satir_rengi(row, apply_minmax, min_val, max_val):
    if row["Izin Durumu"] == "Tam Gün İzinli":
        return "#7FDBFF"
    if row["Izin Durumu"] == "Yarım Gün İzinli":
        return "#D8B4FE"
    if apply_minmax and min_val is not None and max_val is not None and min_val != max_val:
        if row["Toplam Dk"] == max_val:
            return "#90EE90"
        if row["Toplam Dk"] == min_val:
            return "#FF6B6B"
    if apply_minmax and row["Toplam Dk"] < 120:
        return "#FFEB3B"
    return "#FFFFFF"


def satir_notu(row, apply_minmax, min_val, max_val, emoji=True):
    if not apply_minmax or min_val is None or max_val is None or min_val == max_val:
        return ""
    if row["Izin Durumu"] != "Yok":
        return ""
    if row["Toplam Dk"] == max_val:
        return ("🎉 " if emoji else "★ ") + "EN YÜKSEK GÖRÜŞME SÜRESİ"
    if row["Toplam Dk"] == min_val:
        return ("🚨 " if emoji else "⚠ ") + "EN DÜŞÜK ARAMA SÜRESİ"
    return ""


def style_table(df, apply_minmax=True):
    min_val, max_val = hesapla_minmax(df, apply_minmax)
    return style_table_with_minmax(df, apply_minmax, min_val, max_val)


def style_table_with_minmax(df, apply_minmax, min_val, max_val):
    def row_style(row):
        renk = satir_rengi(row, apply_minmax, min_val, max_val)
        return [f"background-color: {renk}"] * len(row)

    return df.style.apply(row_style, axis=1)


# ----------------------------------------------------------------------------------
# YARDIMCI: PNG GORSEL OLUSTURMA
# ----------------------------------------------------------------------------------
def build_table_image(tarih_str, gruplar):
    columns = ["İsim Soyisim", "Arama Sayısı", "Ek Süre (dk)", "Zoiper Süre (dk)", "Toplam Dk", "İzin Durumu", "Not"]
    cell_text, cell_colors, text_colors = [], [], []
    baslik_satirlari = set()
    row_i = 0

    for birim, df, apply_minmax, min_val, max_val in gruplar:
        if df.empty:
            continue
        cell_text.append([birim] + [""] * (len(columns) - 1))
        cell_colors.append(["#374151"] * len(columns))
        text_colors.append(["white"] * len(columns))
        baslik_satirlari.add(row_i)
        row_i += 1

        cell_text.append(columns)
        cell_colors.append(["#E5E7EB"] * len(columns))
        text_colors.append(["#111827"] * len(columns))
        baslik_satirlari.add(row_i)
        row_i += 1

        for _, r in df.iterrows():
            renk = satir_rengi(r, apply_minmax, min_val, max_val)
            not_metni = satir_notu(r, apply_minmax, min_val, max_val, emoji=False)
            satir = [
                str(r["İsim Soyisim"]), str(r["Arama Sayısı"]), str(r["Ek Süre (dk)"]),
                str(r["Zoiper Süre (dk)"]), str(r["Toplam Dk"]), str(r["Izin Durumu"]), not_metni,
            ]
            cell_text.append(satir)
            cell_colors.append([renk] * len(columns))
            text_colors.append(["#111827"] * len(columns))
            row_i += 1

    if not cell_text:
        return None

    fig_height = max(2, len(cell_text) * 0.38 + 0.6)
    fig, ax = plt.subplots(figsize=(13, fig_height))
    ax.axis("off")
    ax.set_title(f"{tarih_str} Tarihli Görüşme Tablosu", fontsize=15, fontweight="bold", loc="left")
    tbl = ax.table(
        cellText=cell_text, cellColours=cell_colors, loc="center", cellLoc="left",
        colWidths=[0.17, 0.11, 0.11, 0.13, 0.11, 0.14, 0.23],
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.scale(1, 1.6)
    for (r, c), cell in tbl.get_celld().items():
        cell.set_edgecolor("#D1D5DB")
        cell.PAD = 0.02
        weight = "bold" if r in baslik_satirlari else "normal"
        cell.set_text_props(weight=weight, color=text_colors[r][c])

    buf = BytesIO()
    plt.savefig(buf, format="png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


# ----------------------------------------------------------------------------------
# YARDIMCI: ORTALAMA RAPOR (HAFTASONU VE TAM GUN IZIN HARIC)
# ----------------------------------------------------------------------------------
def ortalama_rapor(kayitlar_df, uyeler_df, baslangic, bitis):
    if kayitlar_df.empty or uyeler_df.empty:
        return pd.DataFrame()

    df = kayitlar_df.copy()
    df["Tarih_dt"] = pd.to_datetime(df["Tarih"], errors="coerce")
    df = df[(df["Tarih_dt"].dt.date >= baslangic) & (df["Tarih_dt"].dt.date <= bitis)]
    # haftasonlarini cikar (Cumartesi=5, Pazar=6)
    df = df[df["Tarih_dt"].dt.weekday < 5]
    # tam gun izinli gunleri o kisi icin hesaptan tamamen cikar
    df = df[df["Izin Durumu"] != "Tam Gün İzinli"]

    if df.empty:
        return pd.DataFrame()

    ozet = df.groupby("Ad Soyad").agg(
        Gun_Sayisi=("Tarih", "nunique"),
        Toplam_Arama=("Arama Sayisi", "sum"),
        Toplam_Ek_Sure=("Ek Sure Dk", "sum"),
        Toplam_Zoiper_Sure=("Zoiper Sure Dk", "sum"),
        Toplam_Dk=("Toplam Dk", "sum"),
    ).reset_index()

    ozet["Ort. Arama Sayısı"] = (ozet["Toplam_Arama"] / ozet["Gun_Sayisi"]).round(1)
    ozet["Ort. Ek Süre (dk)"] = (ozet["Toplam_Ek_Sure"] / ozet["Gun_Sayisi"]).round(1)
    ozet["Ort. Zoiper Süre (dk)"] = (ozet["Toplam_Zoiper_Sure"] / ozet["Gun_Sayisi"]).round(1)
    ozet["Ort. Toplam Dk"] = (ozet["Toplam_Dk"] / ozet["Gun_Sayisi"]).round(1)

    ozet = ozet.merge(uyeler_df[["Ad Soyad", "Birim"]], on="Ad Soyad", how="left")

    return ozet[["Ad Soyad", "Birim", "Gun_Sayisi", "Ort. Arama Sayısı", "Ort. Ek Süre (dk)", "Ort. Zoiper Süre (dk)", "Ort. Toplam Dk"]].rename(
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

    # ---------------- TAB 1: GUNLUK VERI GIRISI ----------------
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
                        st.markdown(f"**{ad}**  ({dahili})")
                        mevcut_izin = m["Izin Durumu"] if m is not None and "Izin Durumu" in m else "Yok"
                        izin_idx = IZIN_SECENEKLERI.index(mevcut_izin) if mevcut_izin in IZIN_SECENEKLERI else 0
                        c0, c1, c2, c3, c4, c5 = st.columns([1.3, 1, 1, 1, 1, 1])
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
        st.caption("Aşağıdaki tabloyu doğrudan düzenleyebilirsiniz. Birim sütununa: " + ", ".join(BIRIMLER))
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
        st.caption("İki farklı listeyi (Adet ve Süre raporları) aşağıya yapıştırın. Sistem 'MERKEZ' ibaresini kaldırır, süreleri dakikaya çevirir (saniye kalırsa yukarı yuvarlar) ve Dahili numarasına göre eşleştirir.")
        c1, c2 = st.columns(2)
        with c1:
            blok_adet = st.text_area("Arama Sayısı Listesi (Site Adet Dahili Ad Soyad)", height=300, placeholder="MERKEZ 242 4004 DOGUKAN BASARAN")
        with c2:
            blok_sure = st.text_area("Süre Listesi (Site Süre Dahili Ad Soyad)", height=300, placeholder="MERKEZ 03:15:51 4014 Nur Guler")

        if st.button("Ayrıştır"):
            df_adet = parse_block(blok_adet, "Arama Sayısı")
            df_sure = parse_block(blok_sure, "Süre")
            if not df_sure.empty:
                df_sure["Zoiper Süre (dk)"] = df_sure["Süre"].apply(hhmmss_to_dk)
            merged = pd.merge(
                df_adet[["Dahili", "Ad Soyad", "Arama Sayısı"]] if not df_adet.empty else pd.DataFrame(columns=["Dahili", "Ad Soyad", "Arama Sayısı"]),
                df_sure[["Dahili", "Zoiper Süre (dk)"]] if not df_sure.empty else pd.DataFrame(columns=["Dahili", "Zoiper Süre (dk)"]),
                on="Dahili", how="outer",
            )
            # ad soyad bos kalirsa sure listesinden tamamla
            if not df_sure.empty:
                ad_map = dict(zip(df_sure["Dahili"], df_sure["Ad Soyad"]))
                merged["Ad Soyad"] = merged.apply(lambda r: r["Ad Soyad"] if pd.notna(r["Ad Soyad"]) else ad_map.get(r["Dahili"], ""), axis=1)
            merged["Arama Sayısı"] = pd.to_numeric(merged["Arama Sayısı"], errors="coerce").fillna(0).astype(int)
            merged["Zoiper Süre (dk)"] = pd.to_numeric(merged["Zoiper Süre (dk)"], errors="coerce").fillna(0).astype(int)
            merged["Ek Süre (dk)"] = 0
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
                # uyeler listesine yeni kisileri / birimleri isle
                u = load_uyeler()
                for _, r in duzenlenmis2.iterrows():
                    dahili = str(r["Dahili"])
                    ad = r["Ad Soyad"]
                    if u.empty or dahili not in u["Dahili"].values:
                        u = pd.concat([u, pd.DataFrame([{"Ad Soyad": ad, "Dahili": dahili, "Birim": r["Birim"]}])], ignore_index=True)
                    else:
                        u.loc[u["Dahili"] == dahili, "Birim"] = r["Birim"]
                save_uyeler(u)
                # o gunun kayitlarini TEK seferde isle
                kayit_listesi = [
                    {
                        "Ad Soyad": r["Ad Soyad"], "Dahili": str(r["Dahili"]),
                        "Arama Sayisi": r["Arama Sayısı"], "Ek Sure Dk": r["Ek Süre (dk)"],
                        "Zoiper Sure Dk": r["Zoiper Süre (dk)"], "Izin Durumu": r.get("Izin Durumu", "Yok"),
                    }
                    for _, r in duzenlenmis2.iterrows()
                ]
                bulk_upsert_kayitlar(st.session_state.ayristirma_tarih, kayit_listesi)
                del st.session_state.ayristirma_sonuc
                st.success("Liste işlendi ve kaydedildi.")
                st.rerun()

# ----------------------------------------------------------------------------------
# HERKESE ACIK GORUNUM (SADECE O GUNUN TABLOSU)
# ----------------------------------------------------------------------------------
st.markdown("---")
st.subheader(f"📊 {tarih_str} Tarihli Görüşme Tablosu")

if uyeler.empty:
    st.info("Henüz kayıtlı üye yok.")
else:
    gunun_kayitlari = kayitlar[kayitlar["Tarih"] == tarih_str] if not kayitlar.empty else pd.DataFrame(columns=KAYITLAR_HEADERS)

    # onec tum birimlerin gosterim tablolarini hazirla
    birim_gosterimleri = {}
    for birim in BIRIMLER + ["Atanmamış"]:
        grup_uyeler = uyeler[uyeler["Birim"] == birim] if birim != "Atanmamış" else uyeler[~uyeler["Birim"].isin(BIRIMLER)]
        if grup_uyeler.empty:
            continue
        birlesik = grup_uyeler.merge(gunun_kayitlari, on="Ad Soyad", how="left", suffixes=("", "_k"))
        for c, default in [("Arama Sayisi", 0), ("Ek Sure Dk", 0), ("Zoiper Sure Dk", 0), ("Toplam Dk", 0)]:
            birlesik[c] = pd.to_numeric(birlesik[c], errors="coerce").fillna(default).astype(int)
        birlesik["Izin Durumu"] = birlesik["Izin Durumu"].fillna("Yok")

        gosterim = birlesik[["Ad Soyad", "Arama Sayisi", "Ek Sure Dk", "Zoiper Sure Dk", "Toplam Dk", "Izin Durumu"]].rename(columns={
            "Ad Soyad": "İsim Soyisim", "Arama Sayisi": "Arama Sayısı", "Ek Sure Dk": "Ek Süre (dk)",
            "Zoiper Sure Dk": "Zoiper Süre (dk)", "Toplam Dk": "Toplam Dk",
        })
        birim_gosterimleri[birim] = gosterim

    # Retler + Satis Ekibi ayri ayri degil, TEK HAVUZ olarak min/max hesapla
    havuz_parcalari = [df for b, df in birim_gosterimleri.items() if b in RENK_SKALASI_UYGULANAN_BIRIMLER]
    if havuz_parcalari:
        havuz = pd.concat(havuz_parcalari, ignore_index=True)
        genel_min, genel_max = hesapla_minmax(havuz, True)
    else:
        genel_min = genel_max = None

    png_gruplari = []
    for birim, gosterim in birim_gosterimleri.items():
        apply_minmax = birim in RENK_SKALASI_UYGULANAN_BIRIMLER
        min_val, max_val = (genel_min, genel_max) if apply_minmax else (None, None)
        gosterim["Not"] = gosterim.apply(lambda r: satir_notu(r, apply_minmax, min_val, max_val, emoji=True), axis=1)

        st.markdown(f"#### {birim}")
        stil = style_table_with_minmax(gosterim, apply_minmax, min_val, max_val)
        st.dataframe(stil, use_container_width=True, hide_index=True)
        png_gruplari.append((birim, gosterim, apply_minmax, min_val, max_val))

st.caption("🟩 En yüksek görüşme süresi (Retler + Satış Ekibi geneli)   🟥 En düşük görüşme süresi (Retler + Satış Ekibi geneli)   🟨 120 dk altı   🟦 Tam gün izinli   🟪 Yarım gün izinli")

if not uyeler.empty and png_gruplari:
    if st.button("🖼️ Görsel Oluştur (PNG)"):
        png_bytes = build_table_image(tarih_str, png_gruplari)
        if png_bytes:
            st.session_state.png_bytes = png_bytes
            st.session_state.png_tarih = tarih_str
    if "png_bytes" in st.session_state and st.session_state.get("png_tarih") == tarih_str:
        st.image(st.session_state.png_bytes, caption=f"{tarih_str} Görüşme Tablosu")
        st.download_button(
            "📥 PNG olarak bilgisayara indir",
            data=st.session_state.png_bytes,
            file_name=f"gorusme_tablosu_{tarih_str}.png",
            mime="image/png",
        )

# ----------------------------------------------------------------------------------
# ORTALAMA RAPOR (TARIH ARALIGI) - HAFTASONU VE TAM GUN IZIN HARIC
# ----------------------------------------------------------------------------------
st.markdown("---")
st.subheader("📈 Ortalama Rapor (Tarih Aralığı)")
st.caption("Seçilen tarih aralığındaki hafta içi günlerin ortalaması alınır. Hafta sonları ve kişinin tam gün izinli olduğu günler hesaba katılmaz. Yarım gün izinli günler, o günün gerçek verisiyle hesaba dahil edilir.")

rc1, rc2 = st.columns(2)
r_baslangic = rc1.date_input("Başlangıç Tarihi", value=datetime.date.today() - datetime.timedelta(days=7), key="rapor_baslangic")
r_bitis = rc2.date_input("Bitiş Tarihi", value=datetime.date.today() - datetime.timedelta(days=1), key="rapor_bitis")

if st.button("Ortalama Raporu Oluştur"):
    if r_baslangic > r_bitis:
        st.error("Başlangıç tarihi, bitiş tarihinden sonra olamaz.")
    else:
        rapor = ortalama_rapor(kayitlar, uyeler, r_baslangic, r_bitis)
        if rapor.empty:
            st.info("Seçilen aralıkta (hafta sonları ve tam gün izinler hariç) veri bulunamadı.")
        else:
            for birim in BIRIMLER + ["Atanmamış"]:
                grup = rapor[rapor["Birim"] == birim] if birim != "Atanmamış" else rapor[~rapor["Birim"].isin(BIRIMLER)]
                if grup.empty:
                    continue
                st.markdown(f"#### {birim}")
                st.dataframe(grup.drop(columns=["Birim"]), use_container_width=True, hide_index=True)
