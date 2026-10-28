#include "mod/TransportModel.h"

#include <cassert>
#include <string>
#include <vector>

using probe::detail::CommandFramer;

int main() {
    CommandFramer<8> framer;
    std::vector<std::string> lines;
    std::vector<std::size_t> rejected;
    auto feed = [&](std::string_view chunk) {
        framer.feed(
            chunk, [&](auto const& line) { lines.push_back(line); },
            [&](std::size_t bytes) { rejected.push_back(bytes); });
        assert(framer.pendingBytes() <= 8);
    };
    feed("\n\r\none\rtwo");
    assert(lines.empty());
    feed("\r\n12345678\n");
    assert((lines == std::vector<std::string>{"one\rtwo", "12345678"}));
    feed("123456789\nnext\n");
    assert(rejected.size() == 1 && rejected[0] == 9);
    assert(lines.back() == "next" && !framer.discarding());
    feed("12345678");
    feed("9");
    assert(framer.discarding() && rejected.size() == 2);
    for (int i = 0; i < 100; ++i)
        feed("oversize");
    assert(rejected.size() == 2);
    feed("\nok\n\n");
    assert(lines.back() == "ok" && !framer.discarding());
    feed("partial");
    framer.reset();
    feed("new\n");
    assert(lines.back() == "new");
    feed("123456789");
    framer.reset();
    feed("again\n");
    assert(lines.back() == "again");

    // Every split point, including a split UTF-8 code point and CR/LF.
    std::string const stream = "\n\r\na\r\n\xE4\xB8\xAD\nlast\r\n";
    for (std::size_t chunk = 1; chunk <= stream.size(); ++chunk) {
        framer.reset();
        lines.clear();
        for (std::size_t i = 0; i < stream.size(); i += chunk)
            feed(std::string_view(stream).substr(i, chunk));
        assert((lines == std::vector<std::string>{"a", "\xE4\xB8\xAD", "last"}));
        assert(framer.pendingBytes() == 0);
    }

    std::vector<std::string> rows{"a", "bb", "12345678", "", "c"};
    auto original = rows;
    std::string buffer, wire;
    unsigned writes = 0;
    assert(probe::detail::writeLineBatch<8>(rows, buffer, [&](auto const& batch) {
        ++writes;
        wire += batch;
        return true;
    }));
    assert(wire == "a\nbb\n12345678\n\nc\n" && writes == 3);
    rows = original;
    writes = 0;
    assert(!probe::detail::writeLineBatch<8>(rows, buffer, [&](auto const&) { return ++writes != 2; }));
    assert(writes == 2 && rows.back() == "c");
    rows = {"last"};
    wire.clear();
    assert(probe::detail::writeLineBatch<8>(rows, buffer, [&](auto const& batch) {
        wire += batch;
        return true;
    }));
    assert(wire == "last\n");
}
