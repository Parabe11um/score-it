// Bounded checks for CVE-2026-116587. No large RSA validation workload is run.
package main

import (
	"crypto/rand"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"math/big"
	"os"
	"strings"

	jose "github.com/go-jose/go-jose/v3"
)

func must(ok bool, name string) {
	if !ok {
		fmt.Fprintln(os.Stderr, "JOSE_PROBE_REJECTED "+name)
		os.Exit(42)
	}
}

func main() {
	fmt.Println("JOSE_PROBE_READY")
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	must(err == nil, "key generation")
	raw, err := json.Marshal(jose.JSONWebKey{Key: key})
	must(err == nil, "marshal private key")
	var parsed jose.JSONWebKey
	must(json.Unmarshal(raw, &parsed) == nil, "normal private JWK")
	signer, err := jose.NewSigner(jose.SigningKey{Algorithm: jose.RS256, Key: key}, nil)
	must(err == nil, "signer")
	signed, err := signer.Sign([]byte("score-it probe"))
	must(err == nil, "sign")
	compact, err := signed.CompactSerialize()
	must(err == nil, "serialize")
	message, err := jose.ParseSigned(compact)
	must(err == nil, "normal signed JWK")
	body, err := message.Verify(&key.PublicKey)
	must(err == nil && string(body) == "score-it probe", "verify")
	encrypter, err := jose.NewEncrypter(jose.A256GCM, jose.Recipient{Algorithm: jose.RSA_OAEP_256, Key: &key.PublicKey}, nil)
	must(err == nil, "encrypter")
	encrypted, err := encrypter.Encrypt([]byte("score-it encryption"))
	must(err == nil, "encrypt")
	serialized, err := encrypted.CompactSerialize()
	must(err == nil, "serialize encryption")
	enc, err := jose.ParseEncrypted(serialized)
	must(err == nil, "normal encrypted JWK")
	body, err = enc.Decrypt(key)
	must(err == nil && string(body) == "score-it encryption", "decrypt")
	cases := 3
	var normal map[string]interface{}
	must(json.Unmarshal(raw, &normal) == nil, "JWK map")
	oversized := base64.RawURLEncoding.EncodeToString(new(big.Int).Lsh(big.NewInt(1), 4096).Bytes())
	for _, private := range []bool{false, true} {
		m := map[string]interface{}{"kty": "RSA", "n": oversized, "e": "AQAB"}
		if private {
			for _, field := range []string{"d", "p", "q", "dp", "dq", "qi"} {
				m[field] = normal[field]
			}
		}
		data, _ := json.Marshal(m)
		err = json.Unmarshal(data, &parsed)
		must(err != nil && strings.Contains(err.Error(), "modulus too large"), "RSA modulus bound")
		cases++
	}
	normal["p"] = oversized // 513 bytes; bounded even for the old negative control.
	badPrime, _ := json.Marshal(normal)
	err = json.Unmarshal(badPrime, &parsed)
	must(err != nil && strings.Contains(err.Error(), "p * q != n"), "RSA prime consistency")
	cases++
	// All three JWS serializations, including both unprotected header forms.
	public, _ := json.Marshal(jose.JSONWebKey{Key: &key.PublicKey})
	for _, private := range []bool{false, true} {
		jwk := json.RawMessage(public)
		if private {
			jwk = json.RawMessage(raw)
		}
		for _, unprotected := range []bool{false, true} {
			protected := map[string]interface{}{"alg": "RS256"}
			header := map[string]interface{}{}
			if unprotected {
				header["jwk"] = jwk
			} else {
				protected["jwk"] = jwk
			}
			encoded, _ := json.Marshal(protected)
			p := base64.RawURLEncoding.EncodeToString(encoded)
			flat := map[string]interface{}{"protected": p, "payload": "eA", "signature": "eA", "header": header}
			general := map[string]interface{}{"payload": "eA", "signatures": []interface{}{map[string]interface{}{"protected": p, "signature": "eA", "header": header}}}
			messages := []interface{}{flat, general}
			for _, value := range messages {
				data, _ := json.Marshal(value)
				_, err = jose.ParseSigned(string(data))
				must((!private && err == nil) || (private && err != nil && strings.Contains(err.Error(), "JWK contains private parameters")), "embedded JWK before validation")
				cases++
			}
			if !unprotected {
				_, err = jose.ParseSigned(p + ".eA.eA")
				must((!private && err == nil) || (private && err != nil && strings.Contains(err.Error(), "JWK contains private parameters")), "compact private JWK")
				cases++
			}
		}
	}
	fmt.Printf("JOSE_BACKPORT_RUNTIME_OK cases=%d\n", cases)
}
