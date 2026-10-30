// Copyright 2010 The Go Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license in LICENSE.

package json

import (
	"bytes"
	"strings"
	"testing"
)

// Bounded boundary regressions for the upstream a31af25 scanner fix. These
// inputs exercise rejection at 10,001; they never attempt stack exhaustion.
func TestScoreitDepthBoundaries(t *testing.T) {
	for _, object := range []bool{false, true} {
		for _, depth := range []int{9999, 10000, 10001} {
			open, close := "[", "]"
			if object {
				open, close = `{"k":`, "}"
			}
			data := []byte(strings.Repeat(open, depth) + "0" + strings.Repeat(close, depth))
			valid := depth <= 10000
			var raw RawMessage
			if (Unmarshal(data, &raw) == nil) != valid {
				t.Fatalf("object=%v depth=%d: incorrect validity", object, depth)
			}
			var value interface{}
			var compact, indent bytes.Buffer
			for _, err := range []error{
				Unmarshal(data, &value),
				NewDecoder(bytes.NewReader(data)).Decode(&value),
				Compact(&compact, data),
				Indent(&indent, data, "", ""),
			} {
				if (err == nil) != valid || (!valid && !strings.Contains(err.Error(), "exceeded max depth")) {
					t.Fatalf("object=%v depth=%d: unexpected error %v", object, depth, err)
				}
			}
		}
	}
}
