import SwiftUI
import Foundation
import Security
import UIKit

private enum SecureStore {
 static let account = "vn.eri.keyadmin.secret"
 static func read() -> String {
  let q:[String:Any]=[kSecClass as String:kSecClassGenericPassword,kSecAttrAccount as String:account,kSecReturnData as String:true,kSecMatchLimit as String:kSecMatchLimitOne]
  var raw:CFTypeRef?
  guard SecItemCopyMatching(q as CFDictionary,&raw)==errSecSuccess, let d=raw as? Data else{return ""}
  return String(data:d,encoding:.utf8) ?? ""
 }
 static func save(_ value:String) {
  let q:[String:Any]=[kSecClass as String:kSecClassGenericPassword,kSecAttrAccount as String:account]
  let d=Data(value.utf8)
  if SecItemUpdate(q as CFDictionary,[kSecValueData as String:d] as CFDictionary)==errSecItemNotFound {
   var new=q; new[kSecValueData as String]=d
   new[kSecAttrAccessible as String]=kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
   SecItemAdd(new as CFDictionary,nil)
  }
 }
}

struct Item:Decodable,Identifiable {
 let id:String
 let label:String
 let key_suffix:String
 let expires_at:String
 let disabled:Bool
 let device_bound:Bool
 let expired:Bool
}
struct Listed:Decodable {let keys:[Item]}
struct Created:Decodable {let key:String}
struct ApiError:Decodable {let error:String?}

@MainActor final class Model:ObservableObject {
 @Published var secret=SecureStore.read()
 @Published var items:[Item]=[]
 @Published var fresh=""
 @Published var status=""
 @Published var busy=false
 private let api="https://eri-login-api.onrender.com"
 func save(){SecureStore.save(secret);status="Đã lưu khóa Admin trong iOS Keychain"}
 private func request(_ route:String,body:[String:Any]?=nil) async throws -> Data {
  guard secret.count>=32 else {throw NSError(domain:"ERI",code:1,userInfo:[NSLocalizedDescriptionKey:"Hãy nhập ERI_ADMIN_KEY hợp lệ"])}
  guard let u=URL(string:api+route) else{throw URLError(.badURL)}
  var r=URLRequest(url:u);r.timeoutInterval=95
  r.setValue(secret,forHTTPHeaderField:"X-ERI-ADMIN-KEY")
  r.setValue("application/json",forHTTPHeaderField:"Content-Type")
  if let b=body {r.httpMethod="POST";r.httpBody=try JSONSerialization.data(withJSONObject:b)}
  let (data,response)=try await URLSession.shared.data(for:r)
  guard let h=response as? HTTPURLResponse else{throw URLError(.badServerResponse)}
  if !(200...299).contains(h.statusCode) {
   let msg=(try? JSONDecoder().decode(ApiError.self,from:data).error) ?? "HTTP \(h.statusCode)"
   throw NSError(domain:"ERI",code:h.statusCode,userInfo:[NSLocalizedDescriptionKey:msg])
  }
  return data
 }
 func list(app:String) async {
  busy=true;defer{busy=false}
  do {
   var u=URLComponents();u.queryItems=[URLQueryItem(name:"app_id",value:app)]
   let q=u.percentEncodedQuery ?? ""
   let data=try await request("/api/admin/key/list?"+q)
   items=try JSONDecoder().decode(Listed.self,from:data).keys
   status="Đã tải \(items.count) key"
  } catch {status=error.localizedDescription}
 }
 func create(app:String,buyer:String,days:Int) async {
  busy=true;defer{busy=false}
  do {
   let d=try await request("/api/admin/key/create",body:["app_id":app,"label":buyer,"days":days])
   fresh=try JSONDecoder().decode(Created.self,from:d).key
   status="Key đã tạo. Hãy sao chép ngay!"
   await list(app:app)
  }catch{status=error.localizedDescription}
 }
 func change(_ act:String,app:String,id:String,days:Int=30) async {
  busy=true;defer{busy=false}
  do {
   var b:[String:Any]=["app_id":app,"id":id]
   if act=="extend" {b["days"]=days}
   _=try await request("/api/admin/key/"+act,body:b)
   status="Đã thực hiện \(act)"
   await list(app:app)
  }catch{status=error.localizedDescription}
 }
}

struct Home:View {
 @StateObject private var m=Model()
 @AppStorage("eri_app_id") private var app="com.garena.game.kgvo"
 @State private var buyer=""
 @State private var days="30"
 @State private var deleting:String?
 var count:Int{min(3650,max(1,Int(days) ?? 30))}
 var body:some View {
  NavigationStack {
   Form {
    Section("Admin") {
     SecureField("ERI_ADMIN_KEY",text:$m.secret)
       .textInputAutocapitalization(.never).autocorrectionDisabled()
     Button("Lưu khóa trong iOS Keychain"){m.save()}
     TextField("App ID",text:$app).textInputAutocapitalization(.never).autocorrectionDisabled()
    }
    Section("Tạo key 1 thiết bị") {
     TextField("Tên người mua",text:$buyer)
     TextField("Số ngày",text:$days).keyboardType(.numberPad)
     Button("Tạo key"){Task{await m.create(app:app,buyer:buyer,days:count)}}
       .disabled(m.busy)
     if !m.fresh.isEmpty {
      Text("Key chỉ hiển thị đầy đủ một lần").font(.caption)
      Text(m.fresh).font(.callout.monospaced()).textSelection(.enabled)
      Button("Sao chép key"){UIPasteboard.general.string=m.fresh}
      Button("Ẩn key"){m.fresh=""}
     }
    }
    Section("Key online") {
     Button("Làm mới danh sách"){Task{await m.list(app:app)}}.disabled(m.busy)
     ForEach(m.items){item in
      VStack(alignment:.leading,spacing:6){
       Text(item.label.isEmpty ? "Khách chưa đặt tên" : item.label).font(.headline)
       Text("••••\(item.key_suffix) • \(item.device_bound ? "Đã kích hoạt" : "Chưa kích hoạt")")
        .font(.caption).foregroundStyle(.secondary)
       Text("Hạn: \(item.expires_at)").font(.caption)
       Text(item.disabled ? "Bị khóa" : item.expired ? "Hết hạn" : "Đang dùng").font(.caption)
       HStack {
        Button("+\(count) ngày"){Task{await m.change("extend",app:app,id:item.id,days:count)}}
        Button(item.disabled ? "Mở" : "Khóa"){Task{await m.change(item.disabled ? "enable":"disable",app:app,id:item.id)}}
        Button("Reset máy"){Task{await m.change("reset-device",app:app,id:item.id)}}
        Button("Xóa",role:.destructive){deleting=item.id}
       }.buttonStyle(.bordered).font(.caption)
      }.padding(.vertical,4)
     }
    }
    if !m.status.isEmpty {Section("Kết quả"){Text(m.status)}}
    Section{Text("Chỉ quản lý key. Màn hình Login trong game cần được tích hợp vào dự án Unity trước khi build.").font(.footnote)}
   }
   .navigationTitle("ERI KEY ADMIN")
   .alert("Xóa key vĩnh viễn?",isPresented:Binding(get:{deleting != nil},set:{if !$0{deleting=nil}})){
    Button("Hủy",role:.cancel){deleting=nil}
    Button("Xóa",role:.destructive){
     if let id=deleting{Task{await m.change("delete",app:app,id:id)}}
     deleting=nil
    }
   } message:{Text("Key sẽ ngừng hoạt động trên mọi thiết bị.")}
  }
 }
}

@main struct ERIKeyAdminApp:App {
 var body:some Scene{WindowGroup{Home()}}
}