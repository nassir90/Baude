// The rig's pointer: NSEvent +mouseLocation and NSWindow -mouseLocationOutsideOfEventStream answer where the
// synthesized events are (fakeloc_set), not where the owner's real pointer is.
#import <AppKit/AppKit.h>
#import <objc/runtime.h>
static NSPoint screen_at, window_at;
void fakeloc_set(double sx, double sy, double wx, double wy) { screen_at = NSMakePoint(sx, sy); window_at = NSMakePoint(wx, wy); }
static NSPoint ev_loc(id self, SEL _cmd) { return screen_at; }
static NSPoint win_loc(id self, SEL _cmd) { return window_at; }
void fakeloc_install(void) {
    method_setImplementation(class_getClassMethod([NSEvent class], @selector(mouseLocation)), (IMP)ev_loc);
    method_setImplementation(class_getInstanceMethod([NSWindow class], @selector(mouseLocationOutsideOfEventStream)), (IMP)win_loc);
}
