<!-- policy-version: source-review-tr/1.0 -->
# Kaynak inceleme politikası

Oracle DDL Git snapshot'larını yalnız izinli kaynak kanıtlarıyla incele.
Değişiklik açıklamasını risk veya kusur bulgusundan ayrı tut. Kaynakta görülen
olgular ile kaynak destekli çıkarımı ve hipotezi açıkça ayır.

Canlı veritabanına bağlanma, SQL çalıştırma, migration uygulama veya kaynak
dosyasına yazma. Kanıtı olmayan bağımlılık, kullanım, veri hacmi, performans
sonucu, iş amacı veya canlı etki iddiası üretme. Tarihsel referansı güncel
kullanım gibi yazma. Araştırma tamamlanmadıysa `insufficient_context` veya
`partial` durumunu koru.

Exception handler yokluğu, `EXECUTE IMMEDIATE` kullanımı veya index eksikliği
tek başına kusur değildir. Gerekçeyi ilgili kaynak ve doğrulama adımıyla
bağla; genel WHEN OTHERS, ROLLBACK veya performans garantisi önerme.

