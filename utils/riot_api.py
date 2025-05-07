import requests
import json
import os

def download_champion_data():
    # 최신 버전 가져오기
    version_url = "https://ddragon.leagueoflegends.com/api/versions.json"
    versions = requests.get(version_url).json()
    latest_version = versions[0]  # 최신 버전
    
    print(f"최신 버전: {latest_version}")
    
    # 챔피언 데이터 URL
    champion_url = f"https://ddragon.leagueoflegends.com/cdn/{latest_version}/data/ko_KR/champion.json"
    
    # 챔피언 데이터 요청
    response = requests.get(champion_url)
    champion_data = response.json()["data"]
    
    # 이미지 저장 디렉토리 생성
    os.makedirs("static/images", exist_ok=True)
    
    # 챔피언 정보 및 이미지 저장
    champions = []
    
    for champion_id, champion_info in champion_data.items():
        name = champion_info["name"]
        image_filename = champion_info["image"]["full"]
        
        # 이미지 URL
        image_url = f"https://ddragon.leagueoflegends.com/cdn/{latest_version}/img/champion/{image_filename}"
        
        # 이미지 다운로드 및 저장
        image_response = requests.get(image_url)
        image_path = f"static/images/{image_filename}"
        
        with open(image_path, "wb") as image_file:
            image_file.write(image_response.content)
        
        # 챔피언 정보 저장
        champions.append({
            "name": name,
            "image": image_filename
        })
    
    # champions.json 파일로 저장
    with open("champions.json", "w", encoding="utf-8") as f:
        json.dump(champions, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 챔피언 목록과 이미지 저장 완료 (버전: {latest_version})")

if __name__ == "__main__":
    download_champion_data()
