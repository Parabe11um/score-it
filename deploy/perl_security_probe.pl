#!/usr/bin/perl
# Synthetic, bounded regression checks. Executed from stdin/build mounts only.
use strict;
use warnings;
use Archive::Tar;
use File::Temp qw(tempdir);
use Digest::SHA qw(sha256_hex);
use Cwd qw(abs_path);

$| = 1;
sub check ($$) {
    my ($ok, $message) = @_;
    unless ($ok) { print STDERR "PERL_PROBE_REJECTED $message\n"; exit 42; }
}

sub tar_checks {
    print "PERL_TAR_PROBE_READY\n";
    check(defined($Archive::Tar::MAX_FILE_SIZE)
        && $Archive::Tar::MAX_FILE_SIZE == 1024 ** 3, 'default entry size bound');
    my $temp = tempdir(CLEANUP => 1);
    my $big = 'A' x (64 * 1024);
    for my $compression (0, Archive::Tar::COMPRESS_GZIP(), Archive::Tar::COMPRESS_BZIP()) {
        my $path = "$temp/entries-$compression.tar";
        my $writer = Archive::Tar->new;
        $writer->add_data('big.txt', $big);
        $writer->add_data('small.txt', 'following entry');
        check($writer->write($path, $compression), 'write synthetic archive');
        {
            local $Archive::Tar::MAX_FILE_SIZE = 1024;
            my @warnings;
            local $SIG{__WARN__} = sub { push @warnings, @_ };
            my $reader = Archive::Tar->new;
            my @files = $reader->read($path, $compression);
            check(@files == 1 && $files[0]->name eq 'small.txt'
                && $files[0]->get_content eq 'following entry', 'stream alignment after rejection');
            check(@warnings == 1 && $warnings[0] =~ /MAX_FILE_SIZE/, 'one size error per rejected entry');
            @warnings = ();
            my $next = Archive::Tar->iter($path, $compression);
            my @names;
            while (my $entry = $next->()) { push @names, $entry->name; }
            check(@names == 1 && $names[0] eq 'small.txt' && @warnings == 1,
                'iterator preserves alignment and bounded warnings');
        }
        for my $limit (0, length($big), length($big) + 1) {
            local $Archive::Tar::MAX_FILE_SIZE = $limit;
            my $reader = Archive::Tar->new;
            my @files = $reader->read($path, $compression);
            check(@files == 2 && $files[0]->get_content eq $big,
                'normal, exact-boundary and explicit unlimited reads');
        }
    }
    # A tiny input declares 2 GiB. No file or memory allocation of that size.
    my $writer = Archive::Tar->new;
    $writer->add_data('oversized.txt', 'A' x (64 * 1024));
    my $raw = $writer->write;
    my $header = substr($raw, 0, 512);
    substr($header, 124, 12) = sprintf "%011o\0", 2 * 1024 ** 3;
    substr($header, 148, 8) = ' ' x 8;
    substr($header, 148, 8) = sprintf "%06o\0 ", unpack '%16C*', $header;
    substr($raw, 0, 512) = $header;
    my $path = "$temp/truncated-large.tar";
    open my $file, '>', $path or die $!;
    binmode $file; print {$file} $raw; close $file or die $!;
    my @warnings;
    local $SIG{__WARN__} = sub { push @warnings, @_ };
    my $reader = Archive::Tar->new;
    my @files = $reader->read($path);
    check(@files == 0 && @warnings == 1 && $warnings[0] =~ /MAX_FILE_SIZE/,
        'truncated oversized entry stops without rescanning');
    print "PERL_TAR_SIZE_AND_ALIGNMENT_OK\n";
}

sub pod_checks {
    my ($class) = @_;
    (my $path = "$class.pm") =~ s{::}{/}g;
    require $path;
    print "PERL_POD_PROBE_READY=$class\n";
    for my $margin (24, 40) {
        my $formatter = $class->new(width => 24, errors => 'pod');
        $formatter->{MARGIN} = $margin;
        # Exercise Termcap's own regex path, independently of host terminfo.
        @{$formatter}{qw(BOLD UNDL NORM)} = ("\e[1m", "\e[4m", "\e[0m")
            if $class eq 'Pod::Text::Termcap';
        my $text;
        my $ok = eval {
            local $SIG{ALRM} = sub { die "bounded probe timeout\n"; };
            alarm 1;
            $text = $formatter->wrap(('synthetic paragraph ' x 16) . "\n");
            alarm 0;
            1;
        };
        alarm 0;
        check($ok && defined($text) && length($text) < 2048 && $text =~ /^synthetic/,
            "$class bounds output and removes impossible padding");
    }
    # Exercise native POD parsing as well as the formatter's wrap method.
    for my $indent (4, 24) {
        my $output = '';
        my $formatter = $class->new(width => 24, errors => 'pod');
        $formatter->output_string(\$output);
        my $ok = eval {
            local $SIG{ALRM} = sub { die "bounded POD parse timeout\n"; };
            alarm 1;
            $formatter->parse_string_document("=pod\n\n=over $indent\n\n=item *\n\n"
                . ('synthetic paragraph ' x 16) . "\n\n=back\n\n=cut\n");
            alarm 0;
            1;
        };
        alarm 0;
        check($ok && length($output) < 8192 && $output =~ /synthetic/,
            "$class parses normal and over-indented POD");
    }
    print "PERL_POD_MARGIN_OK=$class\n";
}

my $mode = shift(@ARGV) || 'all';
print "PERL_PROBE_UID=$<\n";
tar_checks() if $mode eq 'all' || $mode eq 'tar';
my @formatters = qw(Pod::Text Pod::Text::Color Pod::Text::Overstrike Pod::Text::Termcap);
pod_checks(shift(@ARGV)) if $mode eq 'pod';
if ($mode eq 'all') {
    pod_checks($_) for @formatters;
    for my $module ('Archive::Tar', @formatters) {
        (my $name = "$module.pm") =~ s{::}{/}g;
        open my $file, '<', $INC{$name} or die $!;
        binmode $file;
        my $raw = do { local $/; <$file> };
        print "PERL_MODULE\t$module\t", abs_path($INC{$name}), "\t", sha256_hex($raw), "\n";
    }
    print "PERL_SECURITY_REGRESSION_OK\n";
}
