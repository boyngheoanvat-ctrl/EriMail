
#import <UIKit/UIKit.h>
#import <Security/Security.h>
static UIWindow *eriWindow;
static BOOL eriUnlocked=NO, eriWorking=NO;
static NSString *const eriService=@"vn.eri.license.runtime.v1";
static NSString *eriRead(NSString *key) {
 NSDictionary *q=@{(__bridge id)kSecClass:(__bridge id)kSecClassGenericPassword,(__bridge id)kSecAttrService:eriService,(__bridge id)kSecAttrAccount:key,(__bridge id)kSecReturnData:@YES,(__bridge id)kSecMatchLimit:(__bridge id)kSecMatchLimitOne};
 CFTypeRef ref=NULL; if(SecItemCopyMatching((__bridge CFDictionaryRef)q,&ref)!=errSecSuccess)return @"";
 NSData *d=CFBridgingRelease(ref);return [[NSString alloc]initWithData:d encoding:NSUTF8StringEncoding]?:@"";
}
static void eriWrite(NSString *key,NSString *value) {
 NSDictionary *q=@{(__bridge id)kSecClass:(__bridge id)kSecClassGenericPassword,(__bridge id)kSecAttrService:eriService,(__bridge id)kSecAttrAccount:key};
 NSData *data=[value dataUsingEncoding:NSUTF8StringEncoding];
 if(SecItemUpdate((__bridge CFDictionaryRef)q,(__bridge CFDictionaryRef)@{(__bridge id)kSecValueData:data})==errSecItemNotFound) {
  NSMutableDictionary *params=[q mutableCopy];params[(__bridge id)kSecValueData]=data;
  params[(__bridge id)kSecAttrAccessible]=(__bridge id)kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly;
  SecItemAdd((__bridge CFDictionaryRef)params,NULL);
 }
}
static NSString *eriDeviceID(void) {
 NSString *value=eriRead(@"device-id");
 if([[NSUUID alloc]initWithUUIDString:value])return value;
 value=NSUUID.UUID.UUIDString.lowercaseString;eriWrite(@"device-id",value);return value;
}
@interface ERIGate:UIViewController<UITextFieldDelegate>
@property(nonatomic,strong)UITextField *field;
@property(nonatomic,strong)UILabel *status;
@property(nonatomic,strong)UIButton *button;
-(void)verifySaved;
@end
@implementation ERIGate
-(void)viewDidLoad {
 [super viewDidLoad];
 self.view.backgroundColor=[UIColor colorWithRed:0.025 green:0.054 blue:0.12 alpha:1];
 UIStackView *stack=[UIStackView new];stack.axis=UILayoutConstraintAxisVertical;stack.spacing=22;
 stack.translatesAutoresizingMaskIntoConstraints=NO;[self.view addSubview:stack];
 [NSLayoutConstraint activateConstraints:@[
   [stack.centerXAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.centerXAnchor],
   [stack.centerYAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.centerYAnchor],
   [stack.widthAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.widthAnchor multiplier:.86],
   [stack.widthAnchor constraintLessThanOrEqualToConstant:380]
 ]];
 UILabel *title=[UILabel new];title.text=@"ERI LOGIN";title.font=[UIFont boldSystemFontOfSize:32];
 title.textAlignment=NSTextAlignmentCenter;title.textColor=UIColor.whiteColor;[stack addArrangedSubview:title];
 UILabel *hint=[UILabel new];hint.text=@"1 KEY / 1 THIẾT BỊ";hint.textAlignment=NSTextAlignmentCenter;
 hint.textColor=[UIColor colorWithWhite:.78 alpha:1];[stack addArrangedSubview:hint];
 self.field=[UITextField new];self.field.borderStyle=UITextBorderStyleRoundedRect;
 self.field.backgroundColor=UIColor.whiteColor;self.field.textColor=UIColor.blackColor;
 self.field.autocapitalizationType=UITextAutocapitalizationTypeAllCharacters;
 self.field.autocorrectionType=UITextAutocorrectionTypeNo;
 self.field.adjustsFontSizeToFitWidth=YES;self.field.minimumFontSize=10;
 self.field.placeholder=@"ERI-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX";
 self.field.text=eriRead(@"key");self.field.delegate=self;
 [self.field.heightAnchor constraintEqualToConstant:52].active=YES;
 [stack addArrangedSubview:self.field];
 self.button=[UIButton buttonWithType:UIButtonTypeSystem];
 [self.button setTitle:@"KÍCH HOẠT KEY" forState:UIControlStateNormal];
 self.button.titleLabel.font=[UIFont boldSystemFontOfSize:17];
 [self.button setTitleColor:UIColor.whiteColor forState:UIControlStateNormal];
 self.button.backgroundColor=[UIColor colorWithRed:.15 green:.44 blue:1 alpha:1];
 self.button.layer.cornerRadius=12;
 [self.button.heightAnchor constraintEqualToConstant:52].active=YES;
 [self.button addTarget:self action:@selector(tap:) forControlEvents:UIControlEventTouchUpInside];
 [stack addArrangedSubview:self.button];
 self.status=[UILabel new];self.status.text=@"Nhập key do ERI cấp.";
 self.status.textColor=[UIColor colorWithWhite:.85 alpha:1];self.status.numberOfLines=0;
 self.status.textAlignment=NSTextAlignmentCenter;[stack addArrangedSubview:self.status];
 [self verifySaved];
}
-(BOOL)textFieldShouldReturn:(UITextField *)sender {[sender resignFirstResponder];[self tap:nil];return YES;}
-(void)verifySaved {if(self.field.text.length)[self check:NO];}
-(void)tap:(id)sender {[self check:YES];}
-(void)check:(BOOL)activation {
 if(eriWorking)return;
 NSString *key=[self.field.text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet].uppercaseString;
 if(!key.length){self.status.text=@"Chưa nhập key.";return;}
 NSString *bundle=NSBundle.mainBundle.bundleIdentifier;
 if(!bundle.length){self.status.text=@"Không xác định được ứng dụng.";return;}
 eriWorking=YES;self.button.enabled=NO;self.status.text=@"Đang kiểm tra key...";
 NSURL *url=[NSURL URLWithString:activation?@"https://eri-login-api.onrender.com/api/key/activate":@"https://eri-login-api.onrender.com/api/key/verify"];
 NSMutableURLRequest *req=[NSMutableURLRequest requestWithURL:url];req.HTTPMethod=@"POST";
 req.timeoutInterval=65;[req setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
 req.HTTPBody=[NSJSONSerialization dataWithJSONObject:@{@"app_id":bundle,@"key":key,@"device_id":eriDeviceID()} options:0 error:NULL];
 __weak ERIGate *weakSelf=self;
 [[NSURLSession.sharedSession dataTaskWithRequest:req completionHandler:^(NSData *data,NSURLResponse *res,NSError *error){
  NSDictionary *body=data?[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL]:nil;
  NSInteger code=[res isKindOfClass:NSHTTPURLResponse.class]?[(NSHTTPURLResponse*)res statusCode]:0;
  BOOL allowed=code>=200&&code<300&&[body isKindOfClass:NSDictionary.class]&&[body[@"valid"] boolValue];
  NSString *reason=error?@"Lỗi mạng. Kiểm tra Internet.":([body isKindOfClass:NSDictionary.class]&&[body[@"error"] isKindOfClass:NSString.class]?body[@"error"]:@"Key không hợp lệ.");
  dispatch_async(dispatch_get_main_queue(),^{
   eriWorking=NO;ERIGate *ctl=weakSelf;if(!ctl)return;ctl.button.enabled=YES;
   if(allowed){eriWrite(@"key",key);eriUnlocked=YES;eriWindow.hidden=YES;eriWindow=nil;}
   else ctl.status.text=(!activation&&code==409)?@"Key chưa kích hoạt. Bấm KÍCH HOẠT KEY.":reason;
  });
 }] resume];
}
@end
static void eriShow(int retries) {
 if(eriUnlocked||eriWindow)return;
 UIWindowScene *scene=nil;
 if(@available(iOS 13.0,*)) {
  for(UIScene *s in UIApplication.sharedApplication.connectedScenes)
   if([s isKindOfClass:UIWindowScene.class]&&s.activationState!=UISceneActivationStateUnattached){scene=(UIWindowScene*)s;break;}
 }
 if(!scene){if(retries>0)dispatch_after(dispatch_time(DISPATCH_TIME_NOW,(int64_t)(.35*NSEC_PER_SEC)),dispatch_get_main_queue(),^{eriShow(retries-1);});return;}
 eriWindow=[[UIWindow alloc]initWithWindowScene:scene];eriWindow.windowLevel=UIWindowLevelAlert+100;
 eriWindow.rootViewController=[ERIGate new];eriWindow.hidden=NO;[eriWindow makeKeyAndVisible];
}
__attribute__((constructor))static void eriStart(void){
 dispatch_async(dispatch_get_main_queue(),^{
  [NSNotificationCenter.defaultCenter addObserverForName:UIApplicationDidBecomeActiveNotification object:nil queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification *n){if(!eriUnlocked)eriShow(30);}];
  [NSNotificationCenter.defaultCenter addObserverForName:UISceneDidActivateNotification object:nil queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification *n){if(!eriUnlocked)eriShow(30);}];
  eriShow(30);
 });
}
