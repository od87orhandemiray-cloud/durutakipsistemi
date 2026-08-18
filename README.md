# Duru Takip Sistemi

"Arama Takip Sistemi" ile **aynı özelliklere** sahip, ondan **tamamen bağımsız** ikinci bir uygulama.
Aynı GitHub hesabında farklı bir repo, farklı bir Google E-Tablo ve farklı bir Streamlit uygulaması olarak çalışır — ikisi birbirine karışmaz.

## Kurulum

Kurulum adımları, ilk uygulamayla birebir aynıdır. Tek fark: her şeyi (repo, e-tablo, secrets) **yeni ve ayrı** oluşturmanız.

### 1. Yeni bir GitHub reposu açın
Örn. `durutakipsistemi` adında yeni bir repo oluşturun ve bu klasördeki dosyaları (app.py, requirements.txt, README.md) oraya yükleyin.

### 2. Google tarafı — iki seçenek var:

**Seçenek A (en kolay): Aynı servis hesabını tekrar kullanın**
Daha önce "Arama Takip Sistemi" için oluşturduğunuz servis hesabının `client_email` bilgisini zaten biliyorsunuz. Yeni bir JSON anahtarı oluşturmanıza gerek yok, aynı `gcp_service_account` bilgilerini bu uygulamanın secrets'ına da yapıştırabilirsiniz.

**Seçenek B: Ayrı bir servis hesabı oluşturun**
İsterseniz güvenlik amacıyla bu uygulama için ayrı bir servis hesabı/anahtarı da oluşturabilirsiniz (ilk uygulamanın README'sindeki 1. adımı tekrarlayın).

Hangi seçeneği seçerseniz seçin, **mutlaka yeni ve ayrı bir Google E-Tablo** oluşturun (örn. "Duru Takip Verisi") ve onu kullandığınız servis hesabının `client_email` adresiyle **Düzenleyen (Editor)** olarak paylaşın. Aynı e-tabloyu iki uygulama arasında PAYLAŞMAYIN, yoksa veriler karışır.

### 3. Streamlit Community Cloud'da yayınlayın
1. https://share.streamlit.io → "New app" → bu yeni reponuzu ve `app.py`'yi seçip Deploy edin.
2. Uygulama açıldıktan sonra **⋮ > Settings > Secrets** kısmına girin, `secrets.toml.example` formatındaki kendi bilgilerinizi (admin_password, YENİ sheet_id, gcp_service_account) yapıştırıp kaydedin.
3. Kaydedince uygulama otomatik yeniden başlar.

### 4. Kullanım
Tıpkı ilk uygulama gibi: yönetici şifreyle giriş yapıp veri girer/yönetir, linki paylaştığınız herkes sadece seçili günün tablosunu görür.
