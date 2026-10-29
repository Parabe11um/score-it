#!/usr/bin/perl
use strict;
use warnings;
use utf8;
my ($expected_uid, $expected_version) = @ARGV;
die "Unexpected regex probe UID\n" unless defined($expected_uid) && $< == $expected_uid && $> == $expected_uid && $< != 0;
die "Unexpected Perl ABI\n" unless defined($expected_version) && "$^V" eq "v$expected_version";
my @cases = (
    ['a' x 2048, qr/\A(a+)+\z/, 1],
    [('a' x 256) . 'c', qr/\A((a+)+)+b\z/, 0],
    [('a' x 256) . 'b', qr/\A((a+)+)+b\z/, 1],
    [('ab' x 256) . 'c', qr/\A((ab)+)+b\z/, 0],
    [('ab' x 256), qr/\A((ab)+)+\z/, 1],
    [('я' x 128) . 'c', qr/\A((я+)+)+b\z/, 0],
    [('я' x 128) . 'b', qr/\A((я+)+)+b\z/, 1],
    ['', qr/\A((a+)+)+b\z/, 0],
);
for my $case (@cases) {
    my ($subject,$pattern,$wanted) = @$case;
    my $matched = ($subject =~ $pattern) ? 1 : 0;
    die "Regex regression\n" unless $matched == $wanted;
}
print "PERL_REGEX_RUNTIME_OK cases=" . scalar(@cases) . " uid=$< version=$^V\n";
