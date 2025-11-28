# HTTPS for the home-network demo (docker-compose.nas.yml). Caddy makes its own local certificate
# authority and certificate for the NAS's address, so browsers warn once ("not private") until you
# trust its root certificate (docs/nas-deploy.md). HTTPS makes the app a secure context, as in production.
# NAS_NAME (optional, e.g. mynas.local) adds the NAS's network name to the certificate, so
# https://<name>:8443 works as well as the address; both come from the same local authority.
{
	# browsers send no server name for an IP address, so tell Caddy which certificate to use
	default_sni {$NAS_HOST}
	skip_install_trust
}

https://{$NAS_HOST}, https://{$NAS_NAME:localhost} {
	tls internal

	# uploads and downloads (presigned URLs) on the same address, so there is one certificate to accept
	@documents path /anjung-docs /anjung-docs/*
	handle @documents {
		reverse_proxy s3:9000
	}

	handle {
		reverse_proxy web:80
	}
}
