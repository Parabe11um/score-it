/*-
 * Copyright 2014 Square Inc.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

// Security regressions adapted from go-jose v4.1.6, commit a0111f82742fa92bceec9ede40845e73319e6230.
// Upstream is Apache-2.0. v3 has no allowed-algorithm parameter on ParseSigned.
package jose

import (
	"crypto/rsa"
	"encoding/base64"
	"github.com/go-jose/go-jose/v3/json"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"math/big"
	"strings"
	"testing"
)

func TestRsaKeyChecks(t *testing.T) {
	defer func(old int) { MaxRSAModulusBits = old }(MaxRSAModulusBits)

	// oddOfBits returns an odd integer of exactly the given bit length.
	oddOfBits := func(bits int) *byteBuffer {
		n := new(big.Int).Lsh(big.NewInt(1), uint(bits-1))
		return newBuffer(n.SetBit(n, 0, 1).Bytes())
	}

	testCases := []struct {
		name    string
		public  bool                 // parse as a public key (n and e only)
		limit   int                  // override for MaxRSAModulusBits, 0 for default
		mutate  func(*rawJSONWebKey) // applied to a valid JWK of rsaTestKey
		wantErr string               // substring of the expected error, empty for success
	}{
		{
			name:   "public key, modulus one under limit",
			public: true,
			mutate: func(jwk *rawJSONWebKey) { jwk.N = oddOfBits(MaxRSAModulusBits - 1) },
		},
		{
			name:   "public key, modulus at limit",
			public: true,
			mutate: func(jwk *rawJSONWebKey) { jwk.N = oddOfBits(MaxRSAModulusBits) },
		},
		{
			name:    "public key, modulus over limit",
			public:  true,
			mutate:  func(jwk *rawJSONWebKey) { jwk.N = oddOfBits(MaxRSAModulusBits + 1) },
			wantErr: "modulus too large",
		},
		{
			name:    "private key, modulus over limit",
			mutate:  func(jwk *rawJSONWebKey) { jwk.N = oddOfBits(MaxRSAModulusBits + 1) },
			wantErr: "modulus too large",
		},
		{
			name:    "public key, limit lowered below key",
			public:  true,
			limit:   rsaTestKey.N.BitLen() - 1,
			wantErr: "modulus too large",
		},
		{
			name:    "private key, limit lowered below key",
			limit:   rsaTestKey.N.BitLen() - 1,
			wantErr: "modulus too large",
		},
		{
			name:   "public key, limit raised to key",
			public: true,
			limit:  rsaTestKey.N.BitLen(),
		},
		{
			name:  "private key, limit raised to key",
			limit: rsaTestKey.N.BitLen(),
		},
		{
			name: "p nudged",
			mutate: func(jwk *rawJSONWebKey) {
				jwk.P = newBuffer(new(big.Int).Add(rsaTestKey.Primes[0], big.NewInt(2)).Bytes())
			},
			wantErr: "p * q != n",
		},
		{
			name:    "p = q",
			mutate:  func(jwk *rawJSONWebKey) { jwk.Q = jwk.P },
			wantErr: "p * q != n",
		},
		{
			name:    "p = 1",
			mutate:  func(jwk *rawJSONWebKey) { jwk.P = newBufferFromInt(1) },
			wantErr: "p * q != n",
		},
		{
			name:    "p = 0",
			mutate:  func(jwk *rawJSONWebKey) { jwk.P = newBuffer([]byte{}) },
			wantErr: "p * q != n",
		},
		{
			name:    "enormous p",
			mutate:  func(jwk *rawJSONWebKey) { jwk.P = oddOfBits(1 << 20) },
			wantErr: "p * q != n",
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			MaxRSAModulusBits = 4096
			jwk, err := fromRsaPrivateKey(rsaTestKey)
			require.NoError(t, err)
			if tc.mutate != nil {
				tc.mutate(jwk)
			}
			if tc.limit != 0 {
				MaxRSAModulusBits = tc.limit
			}

			if tc.public {
				_, err = rawJSONWebKey{N: jwk.N, E: jwk.E}.rsaPublicKey()
			} else {
				_, err = jwk.rsaPrivateKey()
			}

			if tc.wantErr == "" {
				assert.NoError(t, err)
			} else if err == nil || !strings.Contains(err.Error(), tc.wantErr) {
				t.Errorf("expected error containing %q, got %v", tc.wantErr, err)
			}
		})
	}
}

func TestUnmarshalPrivateJWKAllowed(t *testing.T) {
	raw, err := json.Marshal(JSONWebKey{Key: rsaTestKey})
	require.NoError(t, err)

	var jwk JSONWebKey
	require.NoError(t, json.Unmarshal(raw, &jwk))
	if _, ok := jwk.Key.(*rsa.PrivateKey); !ok {
		t.Errorf("expected *rsa.PrivateKey, got %T", jwk.Key)
	}

	var jwks JSONWebKeySet
	require.NoError(t, json.Unmarshal([]byte(`{"keys":[`+string(raw)+`]}`), &jwks))
	if len(jwks.Keys) != 1 {
		t.Fatalf("expected 1 key, got %d", len(jwks.Keys))
	}
	if _, ok := jwks.Keys[0].Key.(*rsa.PrivateKey); !ok {
		t.Errorf("expected *rsa.PrivateKey, got %T", jwks.Keys[0].Key)
	}
}

func TestEmbeddedPrivateJWKRejected(t *testing.T) {
	privateJWK, err := fromRsaPrivateKey(rsaTestKey)
	if err != nil {
		t.Fatal(err)
	}
	publicJWK := fromRsaPublicKey(&rsaTestKey.PublicKey)
	strayJWK := fromRsaPublicKey(&rsaTestKey.PublicKey)
	strayJWK.P, strayJWK.Q = privateJWK.P, privateJWK.Q

	testCases := []struct {
		name        string
		jwk         *rawJSONWebKey
		unprotected bool // place the jwk in the unprotected header instead
		wantErr     bool
	}{
		{name: "public key", jwk: publicJWK},
		{name: "private key", jwk: privateJWK, wantErr: true},
		{name: "private key in unprotected header", jwk: privateJWK, unprotected: true, wantErr: true},
		{name: "symmetric key", jwk: &rawJSONWebKey{Kty: "oct", K: newBuffer([]byte("111"))}, wantErr: true},
		{name: "public key with stray private members", jwk: strayJWK, wantErr: true},
	}

	payload := newBuffer([]byte("Lorem ipsum dolor sit amet"))
	signature := newBuffer([]byte("not a real signature"))

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			marshal := func(v interface{}) []byte {
				t.Helper()
				b, err := json.Marshal(v)
				if err != nil {
					t.Fatal(err)
				}
				return b
			}

			alg := json.RawMessage(`"RS256"`)
			jwk := json.RawMessage(marshal(tc.jwk))
			protected := rawHeader{headerAlgorithm: &alg}
			var unprotected *rawHeader
			if tc.unprotected {
				unprotected = &rawHeader{headerJWK: &jwk}
			} else {
				protected[headerJWK] = &jwk
			}
			encProtected := newBuffer(marshal(protected))

			messages := map[string]string{
				"flattened": string(marshal(rawJSONWebSignature{
					Payload:   payload,
					Protected: encProtected,
					Header:    unprotected,
					Signature: signature,
				})),
				"general": string(marshal(rawJSONWebSignature{
					Payload: payload,
					Signatures: []rawSignatureInfo{{
						Protected: encProtected,
						Header:    unprotected,
						Signature: signature,
					}},
				})),
			}
			if !tc.unprotected {
				messages["compact"] = encProtected.base64() + "." + payload.base64() + "." + signature.base64()
			}

			for name, msg := range messages {
				_, err := ParseSigned(msg)
				if tc.wantErr {
					if err == nil || !strings.Contains(err.Error(), "JWK contains private parameters") {
						t.Errorf("%s: expected private parameters error, got %v", name, err)
					}
				} else if err != nil {
					t.Errorf("%s: unexpected error: %v", name, err)
				}
			}
		})
	}
}

func TestGeneralSerializationUnprotectedHeader(t *testing.T) {
	// The unprotected header of each signature must be reflected in the parsed
	// Header and Unprotected fields for both serializations.
	protected := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"RS256"}`))
	payload := base64.RawURLEncoding.EncodeToString([]byte("Lorem ipsum dolor sit amet"))
	signature := base64.RawURLEncoding.EncodeToString([]byte("not a real signature"))
	unprotected := `"header":{"kid":"from-unprotected"}`

	messages := map[string]string{
		"flattened": `{"payload":"` + payload + `","protected":"` + protected + `",` + unprotected + `,"signature":"` + signature + `"}`,
		"general":   `{"payload":"` + payload + `","signatures":[{"protected":"` + protected + `",` + unprotected + `,"signature":"` + signature + `"}]}`,
	}
	for name, msg := range messages {
		t.Run(name, func(t *testing.T) {
			obj, err := ParseSigned(msg)
			if err != nil {
				t.Fatal(err)
			}
			sig := obj.Signatures[0]
			if sig.Header.KeyID != "from-unprotected" {
				t.Errorf("Header.KeyID = %q, want %q", sig.Header.KeyID, "from-unprotected")
			}
			if sig.Unprotected.KeyID != "from-unprotected" {
				t.Errorf("Unprotected.KeyID = %q, want %q", sig.Unprotected.KeyID, "from-unprotected")
			}
		})
	}
}

func TestPrivateEPKRejected(t *testing.T) {
	epk := json.RawMessage(`{"kty":"EC","crv":"P-256","x":"AQAB","y":"AQAB","d":"AQAB"}`)
	headers := rawHeader{headerEPK: &epk}
	if _, err := headers.getEPK(); err == nil || !strings.Contains(err.Error(), "JWK contains private parameters") {
		t.Errorf("expected private parameters error, got %v", err)
	}
}

func (parsed rawHeader) getJWK() (*JSONWebKey, error) {
	v := parsed[headerJWK]
	if v == nil {
		return nil, nil
	}
	var jwk *JSONWebKey
	err := json.Unmarshal(*v, &jwk)
	if err != nil {
		return nil, err
	}
	return jwk, nil
}
