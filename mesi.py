import streamlit as st
import pandas as pd
import os
import pickle
from datetime import datetime
import openpyxl
from io import BytesIO
import io
import base64
import time

# --- Helper Functions (Fungsi Asli Anda - Tidak Diubah) ---

# Fungsi menyimpan session state ke file
def simpan_session_state():
    # Pastikan 'authenticated' disimpan jika ada
    with open("session_state.pkl", "wb") as f:
        pickle.dump(dict(st.session_state), f)

# Fungsi memuat session state dari file
def muat_session_state():
    if os.path.exists("session_state.pkl"):
        try:
            with open("session_state.pkl", "rb") as f:
                data = pickle.load(f)
                for k, v in data.items():
                    if k not in st.session_state:
                        st.session_state[k] = v
        except EOFError:
            st.warning("File session_state.pkl rusak. Mengabaikan...")
            hapus_session_state_file()


# Fungsi untuk menghapus session state file
def hapus_session_state_file():
    if os.path.exists("session_state.pkl"):
        os.remove("session_state.pkl")

# --- Fungsi Excel Anda (Tidak Diubah) ---
def simpan_semua_ke_excel():
    if not st.session_state.get("jurnal"):
        return None, None

    df_jurnal = pd.DataFrame(st.session_state.jurnal)

    # Determine filename
    try:
        tanggal_pertama = pd.to_datetime(df_jurnal["Tanggal"]).min().strftime("%d-%b-%Y")
        filename = f"laporan_keuangan_{tanggal_pertama}.xlsx"
    except Exception:
        filename = "laporan_keuangan_unknown_date.xlsx"

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        # --- JURNAL UMUM ---
        df_jurnal.to_excel(writer, sheet_name="Jurnal Umum", index=False)

        # --- BUKU BESAR ---
        akun_list = df_jurnal["Akun"].unique()
        for akun in akun_list:
            df_akun = df_jurnal[df_jurnal["Akun"] == akun].copy()
            df_akun["Saldo Awal"] = 0 
            df_akun["Mutasi Debit"] = df_akun["Debit"]
            df_akun["Mutasi Kredit"] = df_akun["Kredit"]
            df_akun["Saldo Akhir"] = (df_akun["Mutasi Debit"] - df_akun["Mutasi Kredit"]).cumsum()
            buku_besar_cols = ["Tanggal", "Ref", "Deskripsi", "Mutasi Debit", "Mutasi Kredit", "Saldo Akhir"]
            df_akun['Deskripsi'] = df_akun['Akun'] 
            df_akun.to_excel(writer, sheet_name=f"Buku Besar - {akun[:25]}", index=False, columns=buku_besar_cols)

        # --- NERACA SALDO ---
        neraca_saldo = df_jurnal.groupby(["Akun", "Ref"]).agg(
            Debit=('Debit', 'sum'),
            Kredit=('Kredit', 'sum')
        ).reset_index()
        neraca_saldo['Saldo'] = neraca_saldo['Debit'] - neraca_saldo['Kredit']
        neraca_saldo['Saldo Debit'] = neraca_saldo['Saldo'].apply(lambda x: x if x > 0 else 0)
        neraca_saldo['Saldo Kredit'] = neraca_saldo['Saldo'].apply(lambda x: abs(x) if x < 0 else 0)
        neraca_saldo = neraca_saldo.sort_values(by="Ref")
        cols_neraca_saldo = ["Ref", "Akun", "Saldo Debit", "Saldo Kredit"]
        neraca_saldo[cols_neraca_saldo].to_excel(writer, sheet_name="Neraca Saldo", index=False)

        # --- LAPORAN LABA RUGI ---
        pendapatan_df = df_jurnal[df_jurnal["Akun"].str.contains("Pendapatan", case=False, na=False)].copy()
        beban_listrik_air_df = df_jurnal[df_jurnal["Akun"].str.contains("Beban", case=False, na=False)].copy() # Diperluas untuk semua beban
        
        total_pendapatan_lr = pendapatan_df["Kredit"].sum() - pendapatan_df["Debit"].sum() 
        total_beban_lr = beban_listrik_air_df["Debit"].sum() - beban_listrik_air_df["Kredit"].sum() 

        laba_rugi_data = []
        if total_pendapatan_lr > 0:
            laba_rugi_data.append({"Kategori": "Pendapatan", "Deskripsi": "Total Pendapatan", "Nominal": total_pendapatan_lr})
        
        # Agregasi semua beban
        beban_agg = beban_listrik_air_df.groupby('Akun').agg(Total=('Debit', 'sum')).reset_index()
        for _, row in beban_agg.iterrows():
            if row['Total'] > 0:
                laba_rugi_data.append({"Kategori": "Beban", "Deskripsi": row['Akun'], "Nominal": row['Total']})

        laba_bersih_lr = total_pendapatan_lr - total_beban_lr
        
        if laba_rugi_data:
            df_laba_rugi = pd.DataFrame(laba_rugi_data)
            df_laba_rugi.loc[len(df_laba_rugi)] = ["", "Laba/Rugi Bersih", laba_bersih_lr]
            df_laba_rugi.to_excel(writer, sheet_name="Laporan Laba Rugi", index=False)
        else:
            pd.DataFrame([{"Kategori": "Info", "Deskripsi": "Tidak ada data Laba Rugi", "Nominal": 0}]).to_excel(writer, sheet_name="Laporan Laba Rugi", index=False)

        # --- LAPORAN PERUBAHAN MODAL ---
        current_laba = laba_bersih_lr
        modal_awal_sum = df_jurnal[df_jurnal['Akun'].str.contains('Modal', case=False, na=False)]['Kredit'].sum() - \
                         df_jurnal[df_jurnal['Akun'].str.contains('Modal', case=False, na=False)]['Debit'].sum()
        prive_sum = df_jurnal[df_jurnal['Akun'].str.contains('Prive', case=False, na=False)]['Debit'].sum() - \
                    df_jurnal[df_jurnal['Akun'].str.contains('Prive', case=False, na=False)]['Kredit'].sum()
        ekuitas_akhir = modal_awal_sum + current_laba - prive_sum

        df_perubahan_modal = pd.DataFrame([
            {"Deskripsi": "Modal Awal", "Jumlah": modal_awal_sum},
            {"Deskripsi": "Laba Bersih", "Jumlah": current_laba},
            {"Deskripsi": "Prive", "Jumlah": prive_sum},
            {"Deskripsi": "Modal Akhir", "Jumlah": ekuitas_akhir}
        ])
        df_perubahan_modal.to_excel(writer, sheet_name="Laporan Perubahan Modal", index=False)

        # --- LAPORAN POSISI KEUANGAN (NERACA) ---
        aktiva_lancar_accounts = ['Kas', 'Piutang Usaha', 'Perlengkapan', 'Persediaan']
        aktiva_tetap_accounts = ['Peralatan', 'Akumulasi Penyusutan Peralatan', 'Kendaraan', 'Bangunan']
        kewajiban_accounts = ['Utang Usaha', 'Utang Bank', 'Utang Gaji']
        
        neraca_data = []
        total_aktiva_neraca = 0
        total_pasiva_neraca = 0 

        neraca_data.append({"Kategori": "Aktiva", "Akun": "Aktiva Lancar", "Jumlah": ""})
        for acc in aktiva_lancar_accounts:
            balance = neraca_saldo[neraca_saldo['Akun'] == acc]['Saldo Debit'].sum() - neraca_saldo[neraca_saldo['Akun'] == acc]['Saldo Kredit'].sum()
            if balance != 0:
                neraca_data.append({"Kategori": "Aktiva", "Akun": acc, "Jumlah": balance})
                total_aktiva_neraca += balance

        neraca_data.append({"Kategori": "Aktiva", "Akun": "Aktiva Tetap", "Jumlah": ""})
        for acc in aktiva_tetap_accounts:
            balance = neraca_saldo[neraca_saldo['Akun'] == acc]['Saldo Debit'].sum() - neraca_saldo[neraca_saldo['Akun'] == acc]['Saldo Kredit'].sum()
            if balance != 0:
                neraca_data.append({"Kategori": "Aktiva", "Akun": acc, "Jumlah": balance})
                total_aktiva_neraca += balance
        
        neraca_data.append({"Kategori": "Aktiva", "Akun": "TOTAL AKTIVA", "Jumlah": total_aktiva_neraca})

        neraca_data.append({"Kategori": "Pasiva", "Akun": "Kewajiban", "Jumlah": ""})
        for acc in kewajiban_accounts:
            balance = neraca_saldo[neraca_saldo['Akun'] == acc]['Saldo Kredit'].sum() - neraca_saldo[neraca_saldo['Akun'] == acc]['Saldo Debit'].sum()
            if balance != 0:
                neraca_data.append({"Kategori": "Pasiva", "Akun": acc, "Jumlah": balance})
                total_pasiva_neraca += balance

        neraca_data.append({"Kategori": "Pasiva", "Akun": "Ekuitas", "Jumlah": ""})
        neraca_data.append({"Kategori": "Pasiva", "Akun": "Modal Akhir", "Jumlah": ekuitas_akhir})
        total_pasiva_neraca += ekuitas_akhir

        neraca_data.append({"Kategori": "Pasiva", "Akun": "TOTAL PASIVA", "Jumlah": total_pasiva_neraca})

        df_neraca = pd.DataFrame(neraca_data)
        df_neraca.to_excel(writer, sheet_name="Laporan Posisi Keuangan", index=False)

        # --- JURNAL PENUTUP ---
        income_summary_balance = laba_bersih_lr
        jurnal_penutup_entries = []
        
        if total_pendapatan_lr > 0:
            jurnal_penutup_entries.append({"Tanggal": datetime.today().strftime("%Y-%m-%d"), "Akun": "Pendapatan Usaha", "Debit": total_pendapatan_lr, "Kredit": 0})
            jurnal_penutup_entries.append({"Tanggal": datetime.today().strftime("%Y-%m-%d"), "Akun": "Ikhtisar Laba Rugi", "Debit": 0, "Kredit": total_pendapatan_lr})

        # Menutup semua akun beban
        beban_entries = beban_listrik_air_df.groupby('Akun').agg(TotalDebit=('Debit', 'sum'), TotalKredit=('Kredit', 'sum')).reset_index()
        beban_entries['NetBeban'] = beban_entries['TotalDebit'] - beban_entries['TotalKredit']
        for _, row in beban_entries.iterrows():
            if row['NetBeban'] > 0:
                jurnal_penutup_entries.append({"Tanggal": datetime.today().strftime("%Y-%m-%d"), "Akun": "Ikhtisar Laba Rugi", "Debit": row['NetBeban'], "Kredit": 0})
                jurnal_penutup_entries.append({"Tanggal": datetime.today().strftime("%Y-%m-%d"), "Akun": row['Akun'], "Debit": 0, "Kredit": row['NetBeban']})

        if income_summary_balance != 0:
            if income_summary_balance > 0: # Net Income
                jurnal_penutup_entries.append({"Tanggal": datetime.today().strftime("%Y-%m-%d"), "Akun": "Ikhtisar Laba Rugi", "Debit": income_summary_balance, "Kredit": 0})
                jurnal_penutup_entries.append({"Tanggal": datetime.today().strftime("%Y-%m-%d"), "Akun": "Modal", "Debit": 0, "Kredit": income_summary_balance})
            else: # Net Loss
                jurnal_penutup_entries.append({"Tanggal": datetime.today().strftime("%Y-%m-%d"), "Akun": "Modal", "Debit": abs(income_summary_balance), "Kredit": 0})
                jurnal_penutup_entries.append({"Tanggal": datetime.today().strftime("%Y-%m-%d"), "Akun": "Ikhtisar Laba Rugi", "Debit": 0, "Kredit": abs(income_summary_balance)})

        if prive_sum > 0:
            jurnal_penutup_entries.append({"Tanggal": datetime.today().strftime("%Y-%m-%d"), "Akun": "Modal", "Debit": prive_sum, "Kredit": 0})
            jurnal_penutup_entries.append({"Tanggal": datetime.today().strftime("%Y-%m-%d"), "Akun": "Prive", "Debit": 0, "Kredit": prive_sum})

        if jurnal_penutup_entries:
            df_jurnal_penutup = pd.DataFrame(jurnal_penutup_entries)
            df_jurnal_penutup.to_excel(writer, sheet_name="Jurnal Penutup", index=False)
        else:
             pd.DataFrame([{"Info": "Tidak ada Jurnal Penutup"}]).to_excel(writer, sheet_name="Jurnal Penutup", index=False)

        # --- NERACA SALDO SETELAH PENUTUPAN (NSSP) ---
        df_nssp = neraca_saldo[~neraca_saldo['Akun'].str.contains('Pendapatan|Beban|Prive|Ikhtisar', case=False, na=False)].copy()
        
        if not df_nssp[df_nssp['Akun'].str.contains('Modal', case=False, na=False)].empty:
            df_nssp.loc[df_nssp['Akun'].str.contains('Modal', case=False, na=False), 'Saldo Kredit'] = ekuitas_akhir
            df_nssp.loc[df_nssp['Akun'].str.contains('Modal', case=False, na=False), 'Saldo Debit'] = 0 
        else:
            new_modal_row = pd.DataFrame([{
                "Ref": "300", 
                "Akun": "Modal",
                "Saldo Debit": 0,
                "Saldo Kredit": ekuitas_akhir
            }])
            df_nssp = pd.concat([df_nssp, new_modal_row], ignore_index=True)

        total_nssp_debit = df_nssp["Saldo Debit"].sum()
        total_nssp_kredit = df_nssp["Saldo Kredit"].sum()

        total_nssp_row = pd.DataFrame({
            "Ref": ["TOTAL"], "Akun": [""],
            "Saldo Debit": [total_nssp_debit],
            "Saldo Kredit": [total_nssp_kredit]
        })
        
        df_nssp_final = pd.concat([df_nssp, total_nssp_row], ignore_index=True)
        df_nssp_final.to_excel(writer, sheet_name="NSSP", index=False)

    buffer.seek(0)
    return buffer, filename

# ======================================================================
# --- FUNGSI AUTENTIKASI ---
# ======================================================================
def check_password():
    """Mengembalikan True jika terautentikasi, False jika tidak."""
    
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if st.session_state.authenticated:
        return True

    # --- Tampilkan Form Login jika belum terautentikasi ---
    
    # Atur konfigurasi halaman untuk login (centered)
    st.set_page_config(page_title="Buka Warung - Login", layout="centered")
    
    # --- CSS KHUSUS HALAMAN LOGIN ---
    st.markdown("""
    <style>
        :root {
            --warteg-bg: #FFFFE0;
            --warteg-primary: #FFA500;
            --warteg-text: #8B4513;
            --warteg-header: #006400;
        }
        .stApp {
            background-color: var(--warteg-bg);
        }
        h1 {
            color: var(--warteg-header) !important;
            text-align: center;
        }
        .stMarkdown, .stTextInput label {
            color: var(--warteg-text);
        }
        .stButton button {
            background-color: var(--warteg-primary);
            color: white;
            border: none;
            width: 100%;
        }
        .stError {
            background-color: #FFE0E0;
            border: 1px solid #D2122E;
        }
        /* Style untuk logo agar pas di tengah */
        div[data-testid="stImage"] {
            text-align: center;
        }
        img {
            border-radius: 15px;
        }
    </style>
    """, unsafe_allow_html=True)
    # --------------------------------

    # ======================================================
    # --- PERIKSA JIKA FILE LOGO ADA ---
    # ======================================================
    logo_path = "logo_joma.jpg"
    if os.path.exists(logo_path):
        st.image(logo_path, width=800)
    else:
        st.warning(f"File logo '{logo_path}' tidak ditemukan.")
        st.info(f"Pastikan file logo ada di folder yang sama dengan skrip Python Anda.")
    # ======================================================

    st.title("🔐 Warung Masih Tutup")
    st.markdown("Masukkan **ID Juragan** dan **Sandi Rahasia** untuk buka warung **Laporan Keuangan Warteg Joma**.")

    # --- GANTI USERNAME & KATA SANDI INI ---
    CORRECT_USERNAME = "admin"
    CORRECT_PASSWORD = "wartegjaya" 
    # -------------------------------------

    username = st.text_input("ID Admin", key="login_user")
    password = st.text_input("Sandi Rahasia", type="password", key="login_pass")

    if st.button("Buka Warung! 🍽️"):
        if username == CORRECT_USERNAME and password == CORRECT_PASSWORD:
            st.session_state.authenticated = True
            simpan_session_state()
            st.rerun()
        else:
            st.error("ID Admin atau Sandi salah! Gak jadi jualan hari ini.")
            
    # BARIS INI HARUS DIINDENTASI AGAR BERADA DI DALAM FUNGSI check_password()
    return False

# ======================================================================
# --- FUNGSI TAMBAHAN UNTUK FITUR BARU ---
# ======================================================================

def play_sound_effect():
    """Fungsi untuk memutar efek suara (opsional)"""
    # Kode untuk efek suara akan ditambahkan jika diperlukan
    pass

def get_base64_image(image_path):
    """Mengkonversi gambar ke base64"""
    with open(image_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode()

# ======================================================================
# --- AKHIR BAGIAN FUNGSI AUTENTIKASI ---
# ======================================================================


# --- Streamlit App ---

# 1. Muat session state
muat_session_state()

# 2. Periksa kata sandi. 
if check_password():

    # ======================================================================
    # --- MULAI APLIKASI UTAMA (TEMA WARTEG LENGKAP) ---
    # ======================================================================

    st.set_page_config(page_title="Laporan Keuangan Warteg Joma 🍛", layout="wide", initial_sidebar_state="expanded")

    # --- CSS KUSTOM TEMA WARTEG LENGKAP ---
    st.markdown("""
    <style>
        /* Palet Warna Warteg Lengkap */
        :root {
            --warteg-bg: #FFFFE0;      /* Latar belakang - Kuning Gading (Ivory) */
            --warteg-sidebar-bg: #ADD8E6; /* Sidebar - Biru Muda (Light Blue - 'cat warteg') */
            --warteg-primary: #FFA500;    /* Tombol/Aksen - Oranye (Orange - 'tempe orek') */
            --warteg-secondary: #006400;  /* Hijau Tua - 'daun' */
            --warteg-accent: #8B4513;     /* Coklat Tua - 'kayu' */
            --warteg-text: #2F4F4F;       /* Teks - Dark Slate Gray */
            --warteg-header: #006400;     /* Header - Hijau Tua (Dark Green - 'daun') */
            --warteg-card-bg: #FFFFFF;    /* Kartu/Metric - Putih (White - 'piring') */
            --warteg-card-border: #D3D3D3;/* Border Kartu - Abu-abu (LightGray) */
            --warteg-success: #228B22;    /* Hijau Sukses */
            --warteg-warning: #FF8C00;    /* Oranye Peringatan */
            --warteg-error: #DC143C;      /* Merah Error */
        }

        /* Background Full Page dengan efek keramik warteg */
        .stApp {
            background: linear-gradient(135deg, #FFFFE0 25%, #FFFACD 25%, #FFFACD 50%, #FFFFE0 50%, #FFFFE0 75%, #FFFACD 75%, #FFFACD 100%);
            background-size: 40px 40px;
            animation: moveBackground 20s linear infinite;
            color: var(--warteg-text);
        }

        @keyframes moveBackground {
            0% { background-position: 0 0; }
            100% { background-position: 40px 40px; }
        }

        /* Animasi fade-in untuk konten */
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(20px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .fade-in {
            animation: fadeIn 0.8s ease-in-out;
        }

        /* Sidebar dengan efek kaca */
        [data-testid="stSidebar"] {
            background: linear-gradient(135deg, rgba(173, 216, 230, 0.95) 0%, rgba(135, 206, 250, 0.95) 100%);
            backdrop-filter: blur(10px);
            border-right: 3px solid var(--warteg-accent);
            box-shadow: 5px 0 15px rgba(0, 0, 0, 0.1);
        }

        [data-testid="stSidebar"] h2 {
            color: var(--warteg-header);
            text-align: center;
            font-weight: bold;
            text-shadow: 1px 1px 2px rgba(0,0,0,0.1);
            border-bottom: 2px solid var(--warteg-primary);
            padding-bottom: 10px;
            margin-bottom: 20px;
        }

        /* Tombol dengan efek 3D */
        .stButton button {
            background: linear-gradient(145deg, var(--warteg-primary), #E69500);
            color: white;
            border: none;
            border-radius: 10px;
            font-weight: bold;
            padding: 12px 24px;
            margin: 5px 0;
            box-shadow: 3px 3px 8px rgba(0,0,0,0.2), 
                        -1px -1px 4px rgba(255,255,255,0.5);
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
        }

        .stButton button:hover {
            transform: translateY(-2px);
            box-shadow: 5px 5px 12px rgba(0,0,0,0.3), 
                        -2px -2px 6px rgba(255,255,255,0.6);
            background: linear-gradient(145deg, #FF8C00, var(--warteg-primary));
        }

        .stButton button:active {
            transform: translateY(1px);
            box-shadow: 1px 1px 4px rgba(0,0,0,0.2);
        }

        /* Tombol Logout khusus */
        [data-testid="stSidebar"] .stButton button {
            background: linear-gradient(145deg, #D2122E, #A51024);
            color: white;
        }

        [data-testid="stSidebar"] .stButton button:hover {
            background: linear-gradient(145deg, #A51024, #D2122E);
        }

        /* Judul dengan efek khusus */
        h1, h2, h3 {
            color: var(--warteg-header) !important;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.1);
            border-left: 5px solid var(--warteg-primary);
            padding-left: 15px;
            margin-bottom: 20px;
        }

        /* Input form dengan styling */
        .stTextInput input, .stTextArea textarea, .stDateInput input, .stNumberInput input, .stSelectbox [data-baseweb="select"] {
            border: 2px solid var(--warteg-accent);
            background: rgba(255, 255, 255, 0.9);
            color: var(--warteg-text);
            border-radius: 8px;
            padding: 10px;
            box-shadow: inset 2px 2px 5px rgba(0,0,0,0.1);
            transition: all 0.3s ease;
        }

        .stTextInput input:focus, .stTextArea textarea:focus, .stDateInput input:focus, .stNumberInput input:focus {
            border-color: var(--warteg-primary);
            box-shadow: 0 0 8px rgba(255, 165, 0, 0.3);
            background: white;
        }

        /* Metric cards dengan efek glassmorphism */
        [data-testid="stMetric"] {
            background: linear-gradient(135deg, rgba(255, 255, 255, 0.9) 0%, rgba(255, 250, 205, 0.8) 100%);
            border: 2px solid var(--warteg-primary);
            border-radius: 15px;
            padding: 20px;
            margin: 10px 0;
            box-shadow: 5px 5px 15px rgba(0,0,0,0.1), 
                        -2px -2px 10px rgba(255,255,255,0.5);
            backdrop-filter: blur(10px);
            transition: transform 0.3s ease;
        }

        [data-testid="stMetric"]:hover {
            transform: translateY(-3px);
            box-shadow: 8px 8px 20px rgba(0,0,0,0.15), 
                        -3px -3px 12px rgba(255,255,255,0.6);
        }

        [data-testid="stMetric"] label {
            color: var(--warteg-text);
            font-weight: bold;
            font-size: 1.1em;
        }

        [data-testid="stMetric"] div {
            color: var(--warteg-header);
            font-size: 1.3em;
            font-weight: bold;
        }

        /* Dataframe styling */
        [data-testid="stDataFrame"] {
            border-radius: 10px;
            overflow: hidden;
            box-shadow: 3px 3px 10px rgba(0,0,0,0.1);
        }

        .dataframe {
            border-radius: 10px !important;
        }

        /* Pesan status dengan ikon */
        .stSuccess {
            background: linear-gradient(135deg, #E0FFE0, #B0FFB0);
            border: 2px solid var(--warteg-success);
            border-left: 8px solid var(--warteg-success);
            border-radius: 10px;
            padding: 15px;
            margin: 10px 0;
        }

        .stError {
            background: linear-gradient(135deg, #FFE0E0, #FFB0B0);
            border: 2px solid var(--warteg-error);
            border-left: 8px solid var(--warteg-error);
            border-radius: 10px;
            padding: 15px;
            margin: 10px 0;
        }

        .stInfo {
            background: linear-gradient(135deg, #E6F7FF, #B0E0FF);
            border: 2px solid #1890FF;
            border-left: 8px solid #1890FF;
            border-radius: 10px;
            padding: 15px;
            margin: 10px 0;
        }

        .stWarning {
            background: linear-gradient(135deg, #FFFBE6, #FFE8B0);
            border: 2px solid var(--warteg-warning);
            border-left: 8px solid var(--warteg-warning);
            border-radius: 10px;
            padding: 15px;
            margin: 10px 0;
        }

        /* Radio button styling */
        [data-testid="stSidebar"] .stRadio > div {
            background: rgba(255, 255, 255, 0.8);
            border-radius: 10px;
            padding: 10px;
            margin: 5px 0;
        }

        [data-testid="stSidebar"] .stRadio label {
            color: var(--warteg-text);
            font-weight: 500;
        }

        /* Progress bar styling */
        .stProgress > div > div > div {
            background: linear-gradient(90deg, var(--warteg-primary), var(--warteg-secondary));
        }

        /* Custom scrollbar */
        ::-webkit-scrollbar {
            width: 8px;
        }

        ::-webkit-scrollbar-track {
            background: rgba(173, 216, 230, 0.3);
            border-radius: 10px;
        }

        ::-webkit-scrollbar-thumb {
            background: linear-gradient(var(--warteg-primary), var(--warteg-secondary));
            border-radius: 10px;
        }

        ::-webkit-scrollbar-thumb:hover {
            background: linear-gradient(var(--warteg-secondary), var(--warteg-primary));
        }

        /* Efek hover untuk semua elemen interaktif */
        .element-hover {
            transition: all 0.3s ease;
        }

        .element-hover:hover {
            transform: scale(1.02);
        }

    </style>
    """, unsafe_allow_html=True)

    # --- SIDEBAR DENGAN MENU BARU ---
    st.sidebar.markdown("""
    <div style='text-align: center; padding: 10px;'>
        <h2 style='color: #006400; margin-bottom: 5px;'>🍛 WARTEG JOMA</h2>
        <p style='color: #8B4513; font-size: 0.9em; margin-bottom: 20px;'>Laporan Keuangan Digital</p>
    </div>
    """, unsafe_allow_html=True)

    # --- TOMBOL LOGOUT ---
    st.sidebar.markdown("---")
    if st.sidebar.button("🔒 Tutup Warung (Logout)", use_container_width=True):
        st.session_state.authenticated = False
        simpan_session_state()
        st.rerun()
    st.sidebar.markdown("---")

    # Menu dengan ikon custom
    menu_options = [
        "🏠 Etalase Utama",           # Beranda
        "📝 Buku Pesanan",            # Jurnal Umum  
        "📚 Buku Stok",               # Buku Besar
        "🧮 Hitung Setoran",          # Neraca Saldo
        "💰 Untung Rugi",             # Laporan Laba Rugi
        "📈 Modal Maju Mundur",       # Laporan Perubahan Modal
        "🏦 Harta Karun",             # Laporan Posisi Keuangan
        "🌙 Tutup Warung",            # Jurnal Penutup
        "☀️ Hitungan Besok Pagi",     # NSSP
        "📦 Bungkus Bawa Pulang"      # Unduh Data
    ]

    menu = st.sidebar.radio("🍽️ **MENU UTAMA:**", menu_options, index=0)

    # Initialize session state variables if not already set
    if "modal_awal" not in st.session_state:
        st.session_state.modal_awal = None

    # --- ANIMASI LOADING AWAL ---
    if "page_loaded" not in st.session_state:
        with st.spinner('🔄 Sedang mempersiapkan warung...'):
            time.sleep(1.5)
        st.session_state.page_loaded = True

    # --- Main Content dengan Animasi ---
    st.markdown('<div class="fade-in">', unsafe_allow_html=True)

    if menu == "🏠 Etalase Utama":
        st.title("🍛 Selamat Datang di Warteg Joma!")
        
        # Header dengan animasi
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.markdown("""
            <div style='text-align: center; padding: 20px; background: linear-gradient(135deg, rgba(255,255,255,0.9), rgba(255,250,205,0.8)); 
                     border-radius: 20px; border: 3px solid #FFA500; margin: 20px 0;'>
                <h1 style='color: #006400; margin-bottom: 10px;'>WARTEG JOMA</h1>
                <p style='color: #8B4513; font-size: 1.2em;'>Laporan Keuangan Digital yang Gampang & Cepat</p>
            </div>
            """, unsafe_allow_html=True)
        
        col1, col2 = st.columns([1, 2])
        with col1:
            st.subheader("📌 Tentang Warteg Joma")
            st.markdown("""
            <div style='background: rgba(255,255,255,0.8); padding: 20px; border-radius: 15px; border-left: 5px solid #FFA500;'>
            Aplikasi ini ibarat **kasir pintar** untuk usaha warteg Anda. 
            Dibuat agar UMKM bisa mencatat keuangan dengan **gampang, cepat, dan gak bikin pusing**.
            
            **Fitur Unggulan:**
            🧾 **Buku Pesanan** - Catat semua transaksi
            💰 **Untung Rugi** - Lihat hasil jualan hari ini  
            📦 **Bungkus** - Ambil laporan dalam Excel
            🏦 **Harta Karun** - Cek kekayaan usaha
            </div>
            """, unsafe_allow_html=True)
            
        with col2:
            st.subheader("🛠️ Panduan Masak Laporan")
            with st.expander("📖 Buka Buku Resep (Petunjuk Lengkap)", expanded=True):
                st.markdown("""
                **1. 🧾 BUKU PESANAN (Jurnal Umum)**
                - Catat semua transaksi: jual telur, beli pakan, bayar listrik
                - **WAJIB:** Debit harus sama dengan Kredit!
                
                **2. 📚 BUKU STOK (Buku Besar)**  
                - Rincian uang keluar-masuk per akun
                - Pantau pergerakan Kas, Utang, dll
                
                **3. 🧮 HITUNG SETORAN (Neraca Saldo)**
                - Daftar saldo akhir semua akun
                - Harus SEIMBANG (balance)
                
                **4. 💰 UNTUNG RUGI (Laba Rugi)**
                - Pendapatan - Biaya = Untung/Rugi
                - Tau langsung untung atau buntung
                
                **5. 📈 MODAL MAJU MUNDUR**
                - Modal awal + Untung - Prive = Modal akhir
                - Lihat perkembangan modal usaha
                
                **6. 🏦 HARTA KARUN (Posisi Keuangan)**
                - Melihat semua aset (harta) dan kewajiban (utang + modal) perusahaan.

                **7. 🌙 TUTUP WARUNG (Jurnal Penutup)** 
                - Proses akhir bulan untuk 'mengnolkan' akun pendapatan/beban dan memindahkannya ke modal.

                **8. ☀️ HITUNG BESOK PAGI (NSSP)** 
                - Saldo akhir setelah 'Tutup Warung', siap untuk jualan besok (periode baru).

                **9. 📦 BUNGKUS BAWA PULANG (Unduh Data)** 
                - Ambil semua catatan tadi dalam 1 file Excel.
                """)
                
        # Metrics dashboard
        st.markdown("---")
        st.subheader("📊 Dashboard Cepat")
        
        if "jurnal" in st.session_state and st.session_state.jurnal:
            df_jurnal = pd.DataFrame(st.session_state.jurnal)
            total_debit = df_jurnal["Debit"].sum()
            total_kredit = df_jurnal["Kredit"].sum()
            total_transaksi = len(df_jurnal)
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Total Transaksi", f"{total_transaksi}",
                         help="Jumlah total transaksi yang tercatat")
            with col2:
                st.metric("Total Debit", f"Rp {total_debit:,.0f}",
                         delta="Seimbang" if total_debit == total_kredit else "Tidak Seimbang")
            with col3:
                st.metric("Total Kredit", f"Rp {total_kredit:,.0f}",
                         delta="Seimbang" if total_debit == total_kredit else "Tidak Seimbang")
        else:
            st.info("🔍 Mulai dengan mencatat transaksi pertama di menu 'Buku Pesanan'")

        st.markdown("---")
        st.success("✅ **SELAMAT DATANG!** Silakan pilih menu di sebelah kiri untuk mulai memasak laporan keuangan Anda!")

    # --- JURNAL UMUM ---
    elif menu == "📝 Buku Pesanan":
        st.header("📝 Buku Pesanan (Jurnal Umum)")
        
        if "jurnal" not in st.session_state:
            st.session_state.jurnal = []

        with st.form("form_jurnal", clear_on_submit=True):
            st.subheader("➕ Input Pesanan Baru")
            col1, col2 = st.columns(2)
            with col1:
                tanggal = st.date_input("📅 Tanggal Transaksi", value=datetime.today())
                akun = st.text_input("🏷️ Nama Akun", placeholder="Contoh: Kas, Utang Usaha, Pendapatan")
            with col2:
                ref = st.text_input("🔢 Nomor Ref", placeholder="Contoh: 101, 201, 401")
                keterangan = st.text_input("📋 Keterangan", placeholder="Contoh: Pembelian Bahan Baku")
            
            st.markdown("**💵 Nominal Transaksi**")
            c1, c2 = st.columns(2)
            with c1:
                debit = st.number_input("💰 Debit (Masuk/Biaya)", min_value=0.0, format="%.2f", step=1000.0)
            with c2:
                kredit = st.number_input("💸 Kredit (Keluar/Pendapatan)", min_value=0.0, format="%.2f", step=1000.0)
            
            submitted = st.form_submit_button("✅ Tambahkan ke Buku Pesanan", use_container_width=True)

            if submitted:
                if akun and ref: 
                    if debit == 0 and kredit == 0:
                        st.warning("⚠️ Minimal salah satu nominal (Debit atau Kredit) harus diisi!")
                    else:
                        st.session_state.jurnal.append({
                            "Tanggal": tanggal.strftime("%Y-%m-%d"),
                            "Keterangan": keterangan, 
                            "Akun": akun,
                            "Ref": ref,
                            "Debit": debit,
                            "Kredit": kredit
                        })
                        simpan_session_state()
                        st.success("🎉 Pesanan berhasil dicatat!")
                        time.sleep(0.5)
                        st.rerun()
                else:
                    st.error("❌ Nama Akun dan Nomor Ref harus diisi!")

        if st.session_state.jurnal:
            df_jurnal = pd.DataFrame(st.session_state.jurnal)
            
            st.subheader("📋 Daftar Pesanan Saat Ini")
            st.dataframe(df_jurnal, use_container_width=True)
            
            # Edit data
            with st.expander("✏️ Edit Pesanan (Klik untuk buka)"):
                st.info("Ubah data langsung di tabel bawah, lalu klik 'Simpan Perubahan'")
                df_edit = st.data_editor(df_jurnal, num_rows="dynamic", use_container_width=True, key="edit_jurnal")
                
                if st.button("💾 Simpan Perubahan Pesanan", use_container_width=True):
                    st.session_state.jurnal = df_edit.to_dict(orient="records")
                    simpan_session_state()
                    st.success("✅ Perubahan berhasil disimpan!")
                    time.sleep(1)
                    st.rerun()

            # Summary
            total_debit = df_jurnal["Debit"].sum()
            total_kredit = df_jurnal["Kredit"].sum()

            col1, col2 = st.columns(2)
            col1.metric("📊 Total Debit", f"Rp {total_debit:,.0f}")
            col2.metric("📈 Total Kredit", f"Rp {total_kredit:,.0f}")

            if total_debit == total_kredit:
                st.success("✅ **Buku Pesanan SEIMBANG!** Mantap! 🎉")
            else:
                st.error(f"❌ **Buku Pesanan TIDAK SEIMBANG!** Selisih: Rp {abs(total_debit - total_kredit):,.0f}")

        # Reset button
        if st.session_state.jurnal:
            st.markdown("---")
            if st.button("🗑️ Reset Semua Buku Pesanan", type="secondary", use_container_width=True,
                        help="HATI-HATI! Ini akan menghapus SEMUA catatan dan memulai dari awal!"):
                st.session_state.jurnal = []
                st.session_state.pop("data_laba_rugi", None)
                st.session_state.pop("perubahan_modal", None)
                st.session_state.pop("neraca", None)
                st.session_state.pop("jurnal_penutup", None)
                st.session_state.pop("neraca_saldo_setelah_penutupan", None)
                
                hapus_session_state_file()
                st.success("♻️ Semua catatan pesanan telah direset.")
                time.sleep(1)
                st.rerun()

    # --- BUKU BESAR ---
    elif menu == "📚 Buku Stok":
        st.header("📚 Buku Stok (Buku Besar)")
        
        if "jurnal" not in st.session_state or not st.session_state.jurnal:
            st.info("📭 Buku Pesanan masih kosong. Silakan isi dulu di menu 'Buku Pesanan'.")
        else:
            df_jurnal = pd.DataFrame(st.session_state.jurnal).sort_values(by="Tanggal")
            akun_unik = df_jurnal["Akun"].unique()
            
            col1, col2 = st.columns([2, 1])
            with col1:
                akun_dipilih = st.selectbox("🔍 Pilih Akun untuk Dilihat:", akun_unik)
            with col2:
                st.metric("Jumlah Akun", len(akun_unik))

            df_akun = df_jurnal[df_jurnal["Akun"] == akun_dipilih].copy()
            
            df_akun["Mutasi Debit"] = df_akun["Debit"]
            df_akun["Mutasi Kredit"] = df_akun["Kredit"]
            
            # Logika Saldo Normal
            saldo_normal_debit = True
            akun_kredit_normal = ["utang", "modal", "pendapatan", "kewajiban", "ekuitas", "akumulasi"]
            
            if any(keyword in akun_dipilih.lower() for keyword in akun_kredit_normal):
                saldo_normal_debit = False
                
            if saldo_normal_debit:
                df_akun["Saldo"] = (df_akun["Mutasi Debit"] - df_akun["Mutasi Kredit"]).cumsum()
            else:
                df_akun["Saldo"] = (df_akun["Mutasi Kredit"] - df_akun["Mutasi Debit"]).cumsum()

            st.subheader(f"📊 Rincian Stok: **{akun_dipilih}**")
            st.dataframe(df_akun[["Tanggal", "Keterangan", "Ref", "Mutasi Debit", "Mutasi Kredit", "Saldo"]], 
                        use_container_width=True)

            # Summary metrics
            total_debit_bb = df_akun["Mutasi Debit"].sum()
            total_kredit_bb = df_akun["Mutasi Kredit"].sum()
            saldo_akhir = df_akun['Saldo'].iloc[-1] if not df_akun.empty else 0

            col1, col2, col3 = st.columns(3)
            col1.metric("💵 Total Mutasi Debit", f"Rp {total_debit_bb:,.0f}")
            col2.metric("💸 Total Mutasi Kredit", f"Rp {total_kredit_bb:,.0f}")
            col3.metric("🏦 Saldo Akhir", f"Rp {saldo_akhir:,.0f}",
                       delta="Debit" if saldo_akhir > 0 else "Kredit" if saldo_akhir < 0 else "Nol")

    # --- NERACA SALDO ---
    elif menu == "🧮 Hitung Setoran":
        st.header("🧮 Hitung Setoran (Neraca Saldo)")
        
        if "jurnal" in st.session_state and st.session_state.jurnal:
            df_jurnal = pd.DataFrame(st.session_state.jurnal)

            neraca_saldo = df_jurnal.groupby(["Akun", "Ref"]).agg(
                Debit=('Debit', 'sum'),
                Kredit=('Kredit', 'sum')
            ).reset_index()

            neraca_saldo['Net Saldo'] = neraca_saldo['Debit'] - neraca_saldo['Kredit']
            neraca_saldo['Saldo Debit'] = neraca_saldo['Net Saldo'].apply(lambda x: x if x > 0 else 0)
            neraca_saldo['Saldo Kredit'] = neraca_saldo['Net Saldo'].apply(lambda x: abs(x) if x < 0 else 0)
            neraca_saldo = neraca_saldo.sort_values(by="Ref")
            cols_neraca_saldo = ["Ref", "Akun", "Saldo Debit", "Saldo Kredit"]
            df_saldo_tampil = neraca_saldo[cols_neraca_saldo].copy()

            total_debit_ns = df_saldo_tampil["Saldo Debit"].sum()
            total_kredit_ns = df_saldo_tampil["Saldo Kredit"].sum()

            total_row_ns = pd.DataFrame({
                "Ref": ["**TOTAL SETORAN**"],
                "Akun": [""],
                "Saldo Debit": [total_debit_ns],
                "Saldo Kredit": [total_kredit_ns]
            })

            df_saldo_tampil_final = pd.concat([df_saldo_tampil, total_row_ns], ignore_index=True)
            
            st.subheader("📋 Daftar Saldo Akhir Semua Akun")
            st.dataframe(df_saldo_tampil_final, use_container_width=True)

            # Balance check dengan visual
            col1, col2 = st.columns(2)
            with col1:
                st.metric("💰 Total Debit", f"Rp {total_debit_ns:,.0f}")
            with col2:
                st.metric("💸 Total Kredit", f"Rp {total_kredit_ns:,.0f}")

            if total_debit_ns == total_kredit_ns:
                st.success("""🎉 **HITUNGAN SETORAN SEIMBANG!** 
                \nSemua transaksi tercatat dengan benar. Lanjutkan! ✅""")
            else:
                st.error(f"""❌ **HITUNGAN SETORAN TIDAK SEIMBANG!**
                \n**Selisih:** Rp {abs(total_debit_ns - total_kredit_ns):,.0f}
                \nPeriksa kembali transaksi di Buku Pesanan!""")

        else:
            st.info("📭 Buku Pesanan masih kosong. Isi dulu transaksi di menu 'Buku Pesanan'.")

    # --- LAPORAN LABA RUGI ---
    elif menu == "💰 Untung Rugi":
        st.header("💰 Laporan Untung Rugi (Laba Rugi)")
        
        if "jurnal" not in st.session_state or not st.session_state.jurnal:
            st.info("📭 Buku Pesanan masih kosong. Belum bisa hitung untung rugi.")
        else:
            df_jurnal = pd.DataFrame(st.session_state.jurnal)
            
            # Ambil semua pendapatan
            pendapatan_df = df_jurnal[df_jurnal["Akun"].str.contains("Pendapatan", case=False, na=False)].copy()
            total_pendapatan = pendapatan_df["Kredit"].sum() - pendapatan_df["Debit"].sum()

            # Ambil semua beban
            beban_df = df_jurnal[df_jurnal["Akun"].str.contains("Beban", case=False, na=False)].copy()
            total_beban = beban_df["Debit"].sum() - beban_df["Kredit"].sum() 

            laba_rugi_bersih = total_pendapatan - total_beban
            st.session_state.laba_rugi_bersih = laba_rugi_bersih

            # Tampilan visual dengan columns
            col1, col2, col3 = st.columns(3)
            
            with col1:
                st.metric("📈 Total Pemasukan", f"Rp {total_pendapatan:,.0f}", 
                         delta="Pendapatan" if total_pendapatan > 0 else None)
            
            with col2:
                st.metric("📉 Total Pengeluaran", f"Rp {total_beban:,.0f}",
                         delta="Beban" if total_beban > 0 else None, delta_color="inverse")
            
            with col3:
                if laba_rugi_bersih >= 0:
                    st.metric("🎯 Hasil Akhir", f"Rp {laba_rugi_bersih:,.0f}", 
                             delta="UNTUNG", delta_color="normal")
                else:
                    st.metric("🎯 Hasil Akhir", f"Rp {laba_rugi_bersih:,.0f}", 
                             delta="RUGI", delta_color="off")

            # Detail pendapatan
            with st.expander("📊 Detail Pemasukan", expanded=True):
                if not pendapatan_df.empty:
                    st.dataframe(pendapatan_df[["Tanggal", "Akun", "Keterangan", "Kredit", "Debit"]], 
                                use_container_width=True)
                else:
                    st.info("ℹ️ Belum ada data pendapatan")

            # Detail beban
            with st.expander("📋 Detail Pengeluaran", expanded=True):
                if not beban_df.empty:
                    st.dataframe(beban_df[["Tanggal", "Akun", "Keterangan", "Debit", "Kredit"]], 
                                use_container_width=True)
                else:
                    st.info("ℹ️ Belum ada data beban")

            # Kesimpulan
            st.markdown("---")
            if laba_rugi_bersih > 0:
                st.success(f"""🎉 **SELAMAT! USAHA UNTUNG**
                \n**Keuntungan Bersih:** Rp {laba_rugi_bersih:,.0f}
                \nLanjutkan strategi yang sudah berjalan! ✅""")
            elif laba_rugi_bersih < 0:
                st.error(f"""⚠️ **PERHATIAN! USAHA RUGI**
                \n**Kerugian Bersih:** Rp {abs(laba_rugi_bersih):,.0f}
                \nPeriksa pengeluaran dan tingkatkan penjualan! 🔍""")
            else:
                st.warning("""⚖️ **BREAK EVEN**
                \nPendapatan sama dengan pengeluaran.
                \nButuh peningkatan penjualan untuk mendapat untung! 📈""")

    # --- LAPORAN PERUBAHAN MODAL ---
    elif menu == "📈 Modal Maju Mundur":
        st.header("📈 Modal Maju Mundur (Perubahan Modal)")
        
        if "jurnal" not in st.session_state or not st.session_state.jurnal:
            st.info("📭 Buku Pesanan masih kosong. Modal belum bisa dihitung.")
        else:
            df_jurnal = pd.DataFrame(st.session_state.jurnal)
            laba_bersih = st.session_state.get("laba_rugi_bersih", 0)
            
            # Modal Awal
            modal_entries = df_jurnal[df_jurnal['Akun'].str.contains('Modal', case=False, na=False)]
            modal_awal = modal_entries["Kredit"].sum() - modal_entries["Debit"].sum()
            
            # Prive (Tarikan pribadi)
            prive_entries = df_jurnal[df_jurnal['Akun'].str.contains('Prive', case=False, na=False)]
            total_prive = prive_entries["Debit"].sum() - prive_entries["Kredit"].sum()

            modal_akhir = modal_awal + laba_bersih - total_prive
            st.session_state.modal_akhir_calc = modal_akhir

            # Tampilan dalam bentuk metrics
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                st.metric("💰 Modal Awal", f"Rp {modal_awal:,.0f}")
            with col2:
                st.metric("📊 Untung/Rugi", f"Rp {laba_bersih:,.0f}", 
                         delta="Untung" if laba_bersih > 0 else "Rugi")
            with col3:
                st.metric("💸 Tarikan Prive", f"Rp {total_prive:,.0f}")
            with col4:
                st.metric("🏦 Modal Akhir", f"Rp {modal_akhir:,.0f}",
                         delta=f"{((modal_akhir-modal_awal)/modal_awal*100 if modal_awal !=0 else 0):+.1f}%" if modal_awal != 0 else "Baru")

            # Tabel rincian
            st.subheader("📋 Rincian Perubahan Modal")
            data_perubahan_modal = [
                {"Keterangan": "Modal Awal (Setoran Awal)", "Jumlah (Rp)": modal_awal, "Tipe": "Awal"},
                {"Keterangan": "Untung/Rugi Bersih", "Jumlah (Rp)": laba_bersih, "Tipe": "Penyesuaian"},
                {"Keterangan": "Tarikan Pribadi (Prive)", "Jumlah (Rp)": total_prive * -1, "Tipe": "Pengurang"},
                {"Keterangan": "Modal Akhir (Sekarang)", "Jumlah (Rp)": modal_akhir, "Tipe": "Akhir"}
            ]
            df_perubahan_modal = pd.DataFrame(data_perubahan_modal)
            st.dataframe(df_perubahan_modal, use_container_width=True)

            # Visualisasi progress
            st.markdown("---")
            st.subheader("📊 Progress Modal")
            
            if modal_awal > 0:
                progress = min((modal_akhir / (modal_awal * 2)) * 100, 100) if modal_awal > 0 else 0
                st.progress(int(progress))
                st.caption(f"Progress: {progress:.1f}% dari target 2x modal awal")

    # --- LAPORAN POSISI KEUANGAN (NERACA) ---
    elif menu == "🏦 Harta Karun":
        st.header("🏦 Laporan Harta Karun (Posisi Keuangan/Neraca)")
        
        if "jurnal" not in st.session_state or not st.session_state.jurnal:
            st.info("📭 Buku Pesanan masih kosong. Harta karun belum bisa dilacak.")
        else:
            df_jurnal = pd.DataFrame(st.session_state.jurnal)
            
            # Hitung saldo bersih semua akun
            account_balances = df_jurnal.groupby("Akun").agg(
                Debit=('Debit', 'sum'),
                Kredit=('Kredit', 'sum')
            ).reset_index()
            account_balances['Net Balance'] = account_balances['Debit'] - account_balances['Kredit']
            
            # Ambil Modal Akhir dari perhitungan sebelumnya
            modal_akhir_rp = st.session_state.get("modal_akhir_calc", 0)
            
            # Definisikan Akun-Akun
            aktiva_lancar_accounts = ['Kas', 'Piutang Usaha', 'Perlengkapan', 'Persediaan']
            aktiva_tetap_accounts = ['Peralatan', 'Kendaraan', 'Bangunan', 'Akumulasi Penyusutan']
            kewajiban_accounts = ['Utang Usaha', 'Utang Bank', 'Utang Gaji', 'Utang Pakan']

            total_aktiva = 0
            total_pasiva = 0

            # SISI AKTIVA
            st.subheader("📦 SISI KIRI: HARTA (AKTIVA)")
            
            st.markdown("#### 💰 Harta Lancar (Cepat Jadi Uang)")
            aktiva_lancar_data = []
            for acc in aktiva_lancar_accounts:
                balance = account_balances[account_balances['Akun'].str.lower() == acc.lower()]['Net Balance'].sum()
                if balance != 0: 
                    aktiva_lancar_data.append({"Jenis Harta": acc, "Nilai (Rp)": balance})
                    total_aktiva += balance
            if aktiva_lancar_data:
                st.dataframe(pd.DataFrame(aktiva_lancar_data), use_container_width=True)
            else:
                st.info("ℹ️ Tidak ada data Harta Lancar")

            st.markdown("#### 🏠 Harta Tetap (Aset Jangka Panjang)")
            aktiva_tetap_data = []
            for acc in aktiva_tetap_accounts:
                balance = account_balances[account_balances['Akun'].str.lower() == acc.lower()]['Net Balance'].sum()
                if balance != 0:
                    aktiva_tetap_data.append({"Jenis Harta": acc, "Nilai (Rp)": balance})
                    total_aktiva += balance
            if aktiva_tetap_data:
                st.dataframe(pd.DataFrame(aktiva_tetap_data), use_container_width=True)
            else:
                st.info("ℹ️ Tidak ada data Harta Tetap")
                
            st.metric("📊 Total Harta (Aktiva)", f"Rp {total_aktiva:,.0f}")

            st.markdown("---")

            # SISI PASIVA
            st.subheader("📋 SISI KANAN: UTANG & MODAL (PASIVA)")
            
            st.markdown("#### 💳 Utang (Kewajiban)")
            kewajiban_data = []
            for acc in kewajiban_accounts:
                balance = account_balances[account_balances['Akun'].str.lower() == acc.lower()]['Net Balance'].sum()
                if balance != 0:
                    kewajiban_data.append({"Jenis Utang": acc, "Nilai (Rp)": abs(balance)})
                    total_pasiva += abs(balance)
            if kewajiban_data:
                st.dataframe(pd.DataFrame(kewajiban_data), use_container_width=True)
            else:
                st.info("ℹ️ Tidak ada data Utang")

            st.markdown("#### 💼 Modal (Ekuitas)")
            modal_data = [{"Jenis Modal": "Modal Akhir", "Nilai (Rp)": modal_akhir_rp}]
            st.dataframe(pd.DataFrame(modal_data), use_container_width=True)
            total_pasiva += modal_akhir_rp

            st.metric("📈 Total Utang + Modal (Pasiva)", f"Rp {total_pasiva:,.0f}")

            # Balance check
            st.markdown("---")
            if round(total_aktiva) == round(total_pasiva):
                st.success(f"""✅ **HARTA KARUN SEIMBANG!** 
                \nTotal Kekayaan: Rp {total_aktiva:,.0f}
                \nSemua tercatat dengan benar! 🎉""")
            else:
                st.error(f"""❌ **HARTA KARUN TIDAK SEIMBANG!**
                \n**Selisih:** Rp {abs(total_aktiva - total_pasiva):,.0f}
                \nPeriksa kembali pencatatan transaksi! 🔍""")

    # --- JURNAL PENUTUP ---
    elif menu == "🌙 Tutup Warung":
        st.header("🌙 Proses Tutup Warung (Jurnal Penutup)")
        st.info("""📋 Ini adalah jurnal otomatis untuk menutup akun pendapatan, beban, dan prive ke modal 
        pada akhir periode akuntansi.""")
        
        if "jurnal" not in st.session_state or not st.session_state.jurnal:
            st.info("📭 Buku Pesanan masih kosong. Belum ada yang bisa ditutup.")
        else:
            df_jurnal = pd.DataFrame(st.session_state.jurnal)
            
            jurnal_penutup_entries = []
            closing_date = datetime.today().strftime("%Y-%m-%d")

            laba_rugi_bersih = st.session_state.get("laba_rugi_bersih", 0)

            # 1. Menutup Pendapatan
            pendapatan_accounts = df_jurnal[df_jurnal["Akun"].str.contains("Pendapatan", case=False, na=False)]
            pendapatan_agg = pendapatan_accounts.groupby('Akun').agg(Debit=('Debit', 'sum'), Kredit=('Kredit', 'sum')).reset_index()
            pendapatan_agg['Net'] = pendapatan_agg['Kredit'] - pendapatan_agg['Debit']
            
            for index, row in pendapatan_agg.iterrows():
                if row['Net'] > 0:
                    jurnal_penutup_entries.append({
                        "Tanggal": closing_date, 
                        "Keterangan": f"Penutupan {row['Akun']}", 
                        "Akun": row['Akun'], 
                        "Debit": row['Net'], 
                        "Kredit": 0
                    })
                    jurnal_penutup_entries.append({
                        "Tanggal": closing_date, 
                        "Keterangan": f"Penutupan {row['Akun']}", 
                        "Akun": "Ikhtisar Laba Rugi", 
                        "Debit": 0, 
                        "Kredit": row['Net']
                    })

            # 2. Menutup Beban
            beban_accounts = df_jurnal[df_jurnal["Akun"].str.contains("Beban", case=False, na=False)]
            beban_agg = beban_accounts.groupby('Akun').agg(Debit=('Debit', 'sum'), Kredit=('Kredit', 'sum')).reset_index()
            beban_agg['Net'] = beban_agg['Debit'] - beban_agg['Kredit']
            
            for index, row in beban_agg.iterrows():
                if row['Net'] > 0:
                    jurnal_penutup_entries.append({
                        "Tanggal": closing_date, 
                        "Keterangan": f"Penutupan {row['Akun']}", 
                        "Akun": "Ikhtisar Laba Rugi", 
                        "Debit": row['Net'], 
                        "Kredit": 0
                    })
                    jurnal_penutup_entries.append({
                        "Tanggal": closing_date, 
                        "Keterangan": f"Penutupan {row['Akun']}", 
                        "Akun": row['Akun'], 
                        "Debit": 0, 
                        "Kredit": row['Net']
                    })

            # 3. Menutup Ikhtisar Laba Rugi ke Modal
            if laba_rugi_bersih != 0:
                if laba_rugi_bersih > 0: # Laba
                    jurnal_penutup_entries.append({
                        "Tanggal": closing_date, 
                        "Keterangan": "Penutupan Laba Bersih", 
                        "Akun": "Ikhtisar Laba Rugi", 
                        "Debit": laba_rugi_bersih, 
                        "Kredit": 0
                    })
                    jurnal_penutup_entries.append({
                        "Tanggal": closing_date, 
                        "Keterangan": "Penutupan Laba Bersih", 
                        "Akun": "Modal", 
                        "Debit": 0, 
                        "Kredit": laba_rugi_bersih
                    })
                else: # Rugi
                    jurnal_penutup_entries.append({
                        "Tanggal": closing_date, 
                        "Keterangan": "Penutupan Rugi Bersih", 
                        "Akun": "Modal", 
                        "Debit": abs(laba_rugi_bersih), 
                        "Kredit": 0
                    })
                    jurnal_penutup_entries.append({
                        "Tanggal": closing_date, 
                        "Keterangan": "Penutupan Rugi Bersih", 
                        "Akun": "Ikhtisar Laba Rugi", 
                        "Debit": 0, 
                        "Kredit": abs(laba_rugi_bersih)
                    })

            # 4. Menutup Prive
            prive_entries = df_jurnal[df_jurnal['Akun'].str.contains('Prive', case=False, na=False)]
            total_prive = prive_entries["Debit"].sum() - prive_entries["Kredit"].sum()
            
            if total_prive > 0:
                jurnal_penutup_entries.append({
                    "Tanggal": closing_date, 
                    "Keterangan": "Penutupan Prive", 
                    "Akun": "Modal", 
                    "Debit": total_prive, 
                    "Kredit": 0
                })
                jurnal_penutup_entries.append({
                    "Tanggal": closing_date, 
                    "Keterangan": "Penutupan Prive", 
                    "Akun": "Prive", 
                    "Debit": 0, 
                    "Kredit": total_prive
                })

            if jurnal_penutup_entries:
                df_jp = pd.DataFrame(jurnal_penutup_entries)
                st.subheader("📋 Jurnal Penutup yang Dihasilkan")
                st.dataframe(df_jp, use_container_width=True)
                
                total_debit_jp = df_jp["Debit"].sum()
                total_kredit_jp = df_jp["Kredit"].sum()

                col1, col2 = st.columns(2)
                col1.metric("💰 Total Debit Penutup", f"Rp {total_debit_jp:,.0f}")
                col2.metric("💸 Total Kredit Penutup", f"Rp {total_kredit_jp:,.0f}")

                if round(total_debit_jp) == round(total_kredit_jp):
                    st.success("""✅ **JURNAL PENUTUP SEIMBANG**
                    \nProses tutup warung berhasil dilakukan! 🎉""")
                else:
                    st.error(f"""❌ **JURNAL PENUTUP TIDAK SEIMBANG**
                    \n**Selisih:** Rp {abs(total_debit_jp - total_kredit_jp):,.2f}
                    \nPeriksa kembali perhitungan! 🔍""")
            else:
                st.info("ℹ️ Tidak ada data pendapatan, beban, atau prive yang perlu ditutup.")

    # --- NERACA SALDO SETELAH PENUTUPAN (NSSP) ---
    elif menu == "☀️ Hitungan Besok Pagi":
        st.header("☀️ Hitungan Besok Pagi (NSSP)")
        st.info("""📊 Ini adalah saldo akhir akun-akun permanen (Harta, Utang, Modal) 
        yang akan menjadi saldo awal untuk periode akuntansi berikutnya.""")
        
        if "jurnal" not in st.session_state or not st.session_state.jurnal:
            st.info("📭 Buku Pesanan masih kosong.")
        else:
            df_jurnal = pd.DataFrame(st.session_state.jurnal)
            
            # Ambil saldo awal (Neraca Saldo sebelum penutupan)
            initial_balances = df_jurnal.groupby(["Akun", "Ref"]).agg(
                Debit=('Debit', 'sum'),
                Kredit=('Kredit', 'sum')
            ).reset_index()
            initial_balances['Net Balance'] = initial_balances['Debit'] - initial_balances['Kredit']

            nssp_data = []
            
            # Ambil Modal Akhir yang sudah dihitung
            modal_akhir_calc = st.session_state.get("modal_akhir_calc", 0)
            
            for index, row in initial_balances.iterrows():
                akun = row['Akun']
                ref = row['Ref']
                net_balance = row['Net Balance']
                
                # Filter hanya akun permanen (Bukan Pendapatan, Beban, Prive, Ikhtisar)
                is_permanent_account = not any(keyword in akun.lower() for keyword in ["pendapatan", "beban", "prive", "ikhtisar"])

                if is_permanent_account:
                    if "modal" in akun.lower():
                        # Jika ini akun Modal, ganti saldonya dengan Modal Akhir
                        if modal_akhir_calc >= 0:
                            nssp_data.append({"Ref": ref, "Akun": akun, "Debit": 0, "Kredit": modal_akhir_calc})
                        else:
                            nssp_data.append({"Ref": ref, "Akun": akun, "Debit": abs(modal_akhir_calc), "Kredit": 0})
                    else:
                        # Akun permanen lainnya (Kas, Piutang, Utang, Peralatan, dll)
                        if net_balance >= 0:
                            nssp_data.append({"Ref": ref, "Akun": akun, "Debit": net_balance, "Kredit": 0})
                        else:
                            nssp_data.append({"Ref": ref, "Akun": akun, "Debit": 0, "Kredit": abs(net_balance)})
            
            if nssp_data:
                df_nssp = pd.DataFrame(nssp_data).sort_values(by="Ref")
                
                total_debit_nssp = df_nssp["Debit"].sum()
                total_kredit_nssp = df_nssp["Kredit"].sum()

                total_row_nssp = pd.DataFrame({
                    "Ref": ["**TOTAL**"],
                    "Akun": ["Siap Jualan Besok! 🎉"],
                    "Debit": [total_debit_nssp],
                    "Kredit": [total_kredit_nssp]
                })
                df_nssp_final = pd.concat([df_nssp, total_row_nssp], ignore_index=True)

                st.subheader("📋 Neraca Saldo Setelah Penutupan")
                st.dataframe(df_nssp_final, use_container_width=True)

                # Balance check
                if round(total_debit_nssp) == round(total_kredit_nssp):
                    st.success("""✅ **HITUNGAN BESOK PAGI SEIMBANG!**
                    \nWarung siap buka untuk periode baru! ☀️""")
                else:
                    st.error(f"""❌ **HITUNGAN BESOK PAGI TIDAK SEIMBANG**
                    \n**Selisih:** Rp {abs(total_debit_nssp - total_kredit_nssp):,.2f}""")
            else:
                st.info("ℹ️ Tidak ada data akun permanen yang ditemukan.")

    # --- UNDUH DATA ---
    elif menu == "📦 Bungkus Bawa Pulang":
        st.title("📦 Bungkus Bawa Pulang (Unduh Data)")

        st.info("""💾 Klik tombol di bawah untuk 'membungkus' semua laporan 
        (dari Buku Pesanan sampai Hitungan Besok Pagi) dalam satu file Excel yang rapi.""")

        col1, col2 = st.columns([2, 1])
        with col1:
            if st.button("🎁 Siapkan Bungkusan Excel Lengkap", use_container_width=True):
                with st.spinner("🔄 Lagi dibungkus, Mas/Mba... Mohon tunggu sebentar..."):
                    excel_buffer, filename = simpan_semua_ke_excel()
                    
                    if excel_buffer:
                        st.success(f"""✅ **BUNGKUSAN SIAP!**
                        \nFile '{filename}' berhasil dibuat dan siap diambil!""")
                        
                        st.download_button(
                            label="📥 Klik di Sini untuk Ambil File Excel",
                            data=excel_buffer.getvalue(),
                            file_name=filename,
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True
                        )
                        
                        st.info("""
                        **📋 Yang termasuk dalam bungkusan:**
                        - 📝 Jurnal Umum (Buku Pesanan)
                        - 📚 Buku Besar (Buku Stok)  
                        - 🧮 Neraca Saldo (Hitung Setoran)
                        - 💰 Laporan Laba Rugi (Untung Rugi)
                        - 📈 Laporan Perubahan Modal
                        - 🏦 Laporan Posisi Keuangan (Harta Karun)
                        - 🌙 Jurnal Penutup
                        - ☀️ NSSP (Hitungan Besok Pagi)
                        """)
                    else:
                        st.warning("❌ Tidak ada pesanan di 'Buku Pesanan'. Belum ada yang bisa dibungkus.")

        with col2:
            st.metric("Status Data", 
                     "Siap" if "jurnal" in st.session_state and st.session_state.jurnal else "Kosong",
                     delta="Data tersedia" if "jurnal" in st.session_state and st.session_state.jurnal else "Tidak ada data")

    st.markdown('</div>', unsafe_allow_html=True)

    # ======================================================================
    # --- AKHIR APLIKASI UTAMA ---
    # ======================================================================