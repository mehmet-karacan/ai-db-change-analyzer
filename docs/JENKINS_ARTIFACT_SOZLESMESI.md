# Jenkins artifact sözleşmesi

Analyzer Jenkins üzerinde doğrulanmış artifact üretir. Repository'deki Analyzer
Jenkinsfile, artifact doğrulamasından sonra mevcut `emailext` mekanizmasını
`mkaracan@innova.com.tr` alıcısıyla çağırır. Yeni renderer etkin olduğunda
`--emit-dir` altında aşağıdaki dosyalar oluşur:

Artifact profili için örnek yapılandırma `config/gpu.artifact.example.toml` dosyasıdır.
Bu profilde SMTP bölümü bulunmaz; Analyzer yalnız doğrulanmış artifact'i üretir ve
`result.json` ile tek satır ResultRecord yayınlar. Legacy SMTP akışı için
`config/gpu.example.toml` korunur.

Kaynak üretici akışının ayrıntısı için `GPU_DDL_SYNC_ENTEGRASYON_NOTU.md`
belgesine bakılır: DDL Sync'in geçici TSV dosyaları Analyzer girdisi değildir;
Analyzer `gpu-db` Git snapshot'larını tüketir.

- `report.html`: bağımsız açılabilen, logo verisi gömülü HTML.
- `report-email.html`: e-posta gövdesi için CID kullanan HTML.
- `report.txt`: düz metin karşılığı.
- `report.json`: kanonik analiz kaydı.
- `mail-view.json`: rapor görünümü ve kaynak kanıt bağları.
- `render-manifest.json`: dosya hash'leri, model/görünüm sürümü ve nesne kapsamı.

Jenkins job'ı bu klasörü `archiveArtifacts` ile arşivleyebilir. Yerel
`consume_artifact_directory()` doğrulaması, job'a veya SMTP'ye bağlanmadan önce
bu dosya setini ve manifest hash'lerini kontrol eden mock consumer'dır.

Önerilen Jenkins tüketim adımı:

```groovy
archiveArtifacts artifacts: 'reports/**', fingerprint: true, onlyIfSuccessful: true
```

Mevcut job'da `emailext` çağrısından hemen önce tüketici şu kararları vermelidir:

1. `result.json` içinde `outcome == 'ARTIFACT_READY'`, `artifact_ready == true` ve
   `exit_code == 0` olmasını kontrol et.
2. `report.json`, `mail-view.json` ve `render-manifest.json` içindeki report kimliğini eşleştir; `render-manifest.json` ve `report-email.html` hash/byte doğrulamasını yap.
3. Yalnız bu doğrulamalar geçerse `body: readFile('report-email.html')` ile mevcut
   tek `emailext` çağrısını çalıştır.
4. Stale, mismatch, failed veya partial artifact'te `emailext` çağırma; retry'da
   aynı immutable artifact leaf'ini yeniden kullan.

Artifact hash'i doğrulandıktan sonra consumer, email-ext'in ikinci token
yorumlama katmanını önlemek için yalnız `$BUILD_URL` ve `${JOB_NAME}` biçimindeki
literal token başlangıçlarını `&#36;` olarak escape eder. HTML görünümünde `$`
olarak kalır; model veya kaynak metni Jenkins makrosu çalıştırmaz.

Bu doğrulama ve gönderim, Analyzer Jenkinsfile'ının `post` adımında aşağıdaki
Groovy parçası üzerinden çağrılır; parça ayrı bir sender/job/service oluşturmaz:

Repo içindeki kopya: `jenkins/consume_report_artifact.groovy`.

```groovy
def artifact = 'out'
def result = readJSON file: "${artifact}/result.json"
if (result.outcome != 'ARTIFACT_READY' || result.artifact_ready != true || result.exit_code != 0) {
    error("Analyzer artifact is not sendable: ${result.outcome}/${result.error_code}")
}
def manifest = readJSON file: "${artifact}/render-manifest.json"
def emailHtml = readFile file: "${artifact}/report-email.html", encoding: 'UTF-8'
if (sha256(emailHtml.getBytes('UTF-8')) != manifest.email_html_sha256) {
    error('Analyzer artifact email hash mismatch')
}
emailext(mimeType: 'text/html; charset=UTF-8', body: emailHtml, subject: emailSubject, to: emailRecipients)
```

`sha256(...)` burada mevcut job'ın onaylı yardımcı fonksiyonu veya kurumsal
pipeline kütüphanesiyle sağlanmalıdır; bu belge yeni bir sender/job/service
eklememektedir.

Outlook/SMTP teslimatı, Jenkins job doğrulaması ve canlı model doğrulaması bu
repoda yerel olarak kanıtlanmış sayılmaz; ilgili ortam kapıları `NOT_RUN` veya
`NOT_VERIFIED` kalır.
