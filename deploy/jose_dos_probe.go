// Bounded regressions for CVE-2026-116585/116586. No stack-exhaustion workload.
package main

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/sha256"
	"crypto/sha512"
	"encoding/base64"
	"encoding/binary"
	"fmt"
	"hash"
	"os"
	"strings"

	jose "github.com/go-jose/go-jose/v3"
	josecipher "github.com/go-jose/go-jose/v3/cipher"
	json "github.com/go-jose/go-jose/v3/json"
)

func must(ok bool, reason string) {
	if !ok {
		fmt.Fprintln(os.Stderr, "JOSE_DOS_PROBE_REJECTED "+reason)
		os.Exit(42)
	}
}

// Catch a negative control's panic without crashing the builder. Production code
// must reject the input; recover is used only by this disposable test program.
func safeOpen(aead cipher.AEAD, nonce, ciphertext, aad []byte) (data []byte, err error, panicked bool) {
	defer func() {
		if recover() != nil {
			panicked = true
		}
	}()
	data, err = aead.Open(nil, nonce, ciphertext, aad)
	return
}

func authTag(key, nonce, ciphertext, aad []byte) []byte {
	var newHash func() hash.Hash
	switch len(key) {
	case 32:
		newHash = sha256.New
	case 48:
		newHash = sha512.New384
	case 64:
		newHash = sha512.New
	default:
		panic("test key length")
	}
	mac := hmac.New(newHash, key[:len(key)/2])
	mac.Write(aad)
	mac.Write(nonce)
	mac.Write(ciphertext)
	var size [8]byte
	binary.BigEndian.PutUint64(size[:], uint64(len(aad))*8)
	mac.Write(size[:])
	return mac.Sum(nil)[:len(key)/2]
}

func cbc() {
	cases := 0
	for _, keySize := range []int{32, 48, 64} {
		key := bytes.Repeat([]byte{0x31}, keySize)
		nonce := bytes.Repeat([]byte{0x17}, aes.BlockSize)
		aad := []byte("score-it synthetic aad")
		aead, err := josecipher.NewCBCHMAC(key, aes.NewCipher)
		must(err == nil, "cbc setup")

		_, err, panicked := safeOpen(aead, nonce, authTag(key, nonce, nil, aad), aad)
		must(!panicked, "cbc empty ciphertext panicked")
		must(err != nil && strings.Contains(err.Error(), "padding"), "cbc empty ciphertext not rejected at padding")
		cases++

		short := []byte{1}
		_, err, panicked = safeOpen(aead, nonce, append(short, authTag(key, nonce, short, aad)...), aad)
		must(!panicked && err != nil, "cbc invalid block length")
		cases++

		for _, plaintext := range [][]byte{nil, []byte("score-it ordinary plaintext")} {
			sealed := aead.Seal(nil, nonce, plaintext, aad)
			data, err, panicked := safeOpen(aead, nonce, sealed, aad)
			must(!panicked && err == nil && bytes.Equal(data, plaintext), "cbc ordinary roundtrip")
			cases++
		}
		badTag := authTag(key, nonce, nil, aad)
		badTag[0] ^= 1
		_, err, panicked = safeOpen(aead, nonce, badTag, aad)
		must(!panicked && err != nil && strings.Contains(err.Error(), "auth tag"), "cbc invalid auth tag")
		cases++
	}
	fmt.Printf("JOSE_DOS_CBC_RUNTIME_OK cases=%d\n", cases)
}

func nested(kind string, depth int) string {
	if kind == "array" {
		return strings.Repeat("[", depth) + "0" + strings.Repeat("]", depth)
	}
	return strings.Repeat(`{"k":`, depth) + "0" + strings.Repeat("}", depth)
}

func parseSigned(raw string) (*jose.JSONWebSignature, error) {
	return jose.ParseSigned(raw)
}

func parseEncrypted(raw string) (*jose.JSONWebEncryption, error) {
	return jose.ParseEncrypted(raw)
}

func jsonDepth() {
	cases := 0
	for _, kind := range []string{"array", "object"} {
		for _, depth := range []int{9999, 10000, 10001} {
			data := []byte(nested(kind, depth)) // Largest input: 60,007 bytes.
			valid := depth <= 10000
			var raw json.RawMessage
			must((json.Unmarshal(data, &raw) == nil) == valid, "json depth limit not enforced")
			cases++
			var value interface{}
			var compact, indent bytes.Buffer
			errors := []error{
				json.Unmarshal(data, &value),
				json.NewDecoder(bytes.NewReader(data)).Decode(&value),
				json.Compact(&compact, data),
				json.Indent(&indent, data, "", ""),
			}
			for _, err := range errors {
				if valid {
					must(err == nil, "json accepted boundary")
				} else {
					must(err != nil && strings.Contains(err.Error(), "exceeded max depth"), "json over-limit error")
				}
				cases++
			}
		}
		// Exercise public JOSE JSON entry points as well as their JSON package.
		deep := nested(kind, 10001)
		protected := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"RS256"}`))
		_, err := parseSigned(`{"protected":"` + protected + `","payload":"eA","signature":"eA","extra":` + deep + `}`)
		must(err != nil && strings.Contains(err.Error(), "exceeded max depth"), "jws over-limit JSON")
		cases++
		_, err = parseEncrypted(`{"extra":` + deep + `}`)
		must(err != nil && strings.Contains(err.Error(), "exceeded max depth"), "jwe over-limit JSON")
		cases++
	}
	fmt.Printf("JOSE_DOS_JSON_RUNTIME_OK cases=%d max_depth=10000\n", cases)
}

func main() {
	if len(os.Args) != 2 {
		panic("test mode required")
	}
	fmt.Println("JOSE_DOS_PROBE_READY mode=" + os.Args[1])
	switch os.Args[1] {
	case "cbc":
		cbc()
	case "json":
		jsonDepth()
	default:
		panic("unknown test mode")
	}
}
