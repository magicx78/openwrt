# Provisioning & Custom Build Hinweise

## 1. Benötigte Pakete im Custom Image

Damit das Provisioning-Script korrekt funktioniert, muss `curl` im Image
enthalten sein.\
Das Script verwendet JSON-POST Requests, die mit BusyBox-`wget` nicht
zuverlässig möglich sind.

### Beispiel Build-Befehl

``` bash
make image PROFILE="DEIN_PROFIL" \
PACKAGES="wpad-wolfssl kmod-batman-adv batctl-full openssh-sftp-server curl -wpad-basic-mbedtls"
```

`curl` zieht notwendige Abhängigkeiten wie `libcurl` und `ca-bundle`
automatisch.

Hinweis:\
Für SFTP wird ein laufender SSH-Server benötigt.\
Bei Verwendung von Dropbear reicht `openssh-sftp-server` nicht aus.\
Für vollwertiges SFTP muss `openssh-server` verwendet werden.

------------------------------------------------------------------------

## 2. Provisioning Script Deployment

Das Provisioning-Script wird über `/etc/uci-defaults/` eingebunden.\
Scripte in diesem Verzeichnis werden beim ersten Boot automatisch
ausgeführt.

### Upload auf den Router

``` bash
scp 99-provision.sh root@192.168.1.1:/etc/uci-defaults/99-provision
scp provision.conf  root@192.168.1.1:/etc/provision.conf
ssh root@192.168.1.1 'chmod +x /etc/uci-defaults/99-provision'
```

Voraussetzung:\
Ein SSH-Server (Dropbear oder OpenSSH) muss auf Port 22 laufen.

------------------------------------------------------------------------

## 3. Manuelles Testen des Provisionings

Da `/etc/uci-defaults/` nur beim ersten Boot ausgeführt wird, kann das
Script manuell getestet werden:

``` bash
ssh root@192.168.1.1 '/etc/uci-defaults/99-provision'
```

Log-Ausgabe prüfen:

``` bash
ssh root@192.168.1.1 'tail -n 100 /tmp/provision.log'
```

------------------------------------------------------------------------

## 4. Typische Fehlerquellen

-   `curl` fehlt → JSON-POST nicht möglich\
-   Script nicht ausführbar (`chmod +x` vergessen)\
-   Router bereits provisioniert (`/etc/provisioned` existiert)\
-   Kein laufender SSH-Server
