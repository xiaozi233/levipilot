#include "mod/LifecycleModel.h"

#include <cassert>

int main() {
    probe::detail::LifecycleCounters counters;
    probe::detail::observeLifecycleLine("01:02:03.004 enable: begin", counters);
    assert(counters.generation == 1 && counters.disable == 0);
    probe::detail::observeLifecycleLine("01:02:03.005 enable: done", counters);
    probe::detail::observeLifecycleLine("01:02:03.006 enable: begin lifecycle=2", counters);
    assert(counters.generation == 2 && counters.disable == 0);
    probe::detail::observeLifecycleLine("01:02:03.007 enable: done lifecycle=2", counters);
    probe::detail::observeLifecycleLine("01:02:03.008 disable: begin lifecycle=2 disable_id=1", counters);
    assert(counters.disable == 0);
    probe::detail::observeLifecycleLine("01:02:03.009 disable: done lifecycle=2 disable_id=1 ok=1", counters);
    assert(counters.disable == 1);
    probe::detail::observeLifecycleLine("01:02:03.010 disable: done lifecycle=2 disable_id=4 ok=1", counters);
    assert(counters.disable == 4);
    probe::detail::observeLifecycleLine("01:02:03.011 disable: done lifecycle=2 disable_id=3 ok=1", counters);
    assert(counters.disable == 4);
    probe::detail::observeLifecycleLine("01:02:03.012 disable: done lifecycle=2 disable_id=bad", counters);
    assert(counters.disable == 4);
    probe::detail::observeLifecycleLine("01:02:03.013 disable: done lifecycle=2", counters);
    assert(counters.generation == 2 && counters.disable == 4);
}
