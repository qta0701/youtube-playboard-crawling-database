import os

NEW_CODE = """        if start_btn:
            st.session_state['crawl_result'] = None  # 이전 크롤링 결과 초기화
            st.session_state['log_history'] = []  # 새로 크롤링 시작 시 로그 초기화
            st.session_state['stop_requested'] = False
            st.session_state['resume_requested'] = False
            st.session_state['skip_requested'] = False
            
            streamlit_handler = StreamlitLogHandler(log_shell)
            streamlit_handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)-8s - %(message)s', '%H:%M:%S'))
            logger.addHandler(streamlit_handler)
            
            try:
                ranking_date = specific_date if specific_date else datetime.now().strftime('%Y-%m-%d')
                calc_ranking_date = ranking_date if use_specific_date else get_actual_ranking_date_str(ranking_date)
                target_count = crawl_limit
                
                # 수집할 수집 기준 목록 결정
                active_criteria_list = ["조회수 순위", "좋아요 순위", "댓글 순위"] if crawl_all_criteria else [crawl_criteria]
                all_categories = get_category_list()
                
                # 통계 트래킹 누적용 변수들 (criteria 루프 전역)
                total_target_cats_count = 0
                total_updated_files_count = 0
                total_updated_rows_count = 0
                total_newly_crawled_rows = 0
                total_failed_cats_list = []
                total_skip_cats_count = 0
                
                # criteria 루프 진행 수
                total_steps = len(active_criteria_list) * (len(all_categories) if batch_mode else 1)
                current_step = 0
                
                # 대량의 레코드들을 모아둘 dict/list 버퍼
                all_combined_data = []
                last_filepath = None
                last_filename = None
                
                logger.info("=" * 60)
                logger.info("🚀 [크롤링 기동 옵션 디버그]")
                logger.info(f"  - 수집 대상 (Target Type)  : {target_type}")
                logger.info(f"  - 배치 모드 (Batch Mode)   : {batch_mode}")
                logger.info(f"  - 모든 수집기준 일괄 기동   : {crawl_all_criteria} (기준들: {active_criteria_list})")
                logger.info(f"  - 카테고리 (Category)      : {category if not batch_mode else '전체 일괄'}")
                logger.info(f"  - 국가 (Country)           : {country}")
                logger.info(f"  - 기간 (Period)            : {period}")
                logger.info(f"  - 특정 날짜 수집 (Use Date): {use_specific_date} (날짜: {ranking_date})")
                logger.info(f"  - 로그인 모드 (Login Mode) : {login_mode}")
                logger.info(f"  - 수집 제한 개수 (Limit)   : {target_count}")
                logger.info("=" * 60)
                
                timestamp = None
                if use_specific_date and specific_date:
                    dt = datetime.strptime(specific_date, '%Y-%m-%d')
                    timestamp = int(dt.timestamp())
                
                crawler = PlayboardCrawler(headless=Config.CHROME_HEADLESS)
                crawler.skip_requested = False
                st.session_state['crawler_instance'] = crawler
                
                for crit_idx, cur_crit in enumerate(active_criteria_list):
                    if st.session_state['stop_requested']:
                        logger.info("🛑 사용자 중단 감지: 수집 기준 순회를 취소합니다.")
                        break
                        
                    logger.info(f"🔄 [수집 기준 순회 시작] {cur_crit} ({crit_idx+1}/{len(active_criteria_list)})")
                    
                    if not batch_mode:
                        status_text.info(f"[{cur_crit}] 크롤링 시작 중: {target_type} / {category} / {country} / {period}...")
                        progress_bar.progress(current_step / total_steps)
                        
                        url = build_url(target_type, category, country, period, timestamp)
                        logger.info(f"[{cur_crit}] Built URL: {url}")
                        
                        existing_filepath = find_existing_batch_file(
                            base_dir=Config.OUTPUT_DIR,
                            target_type=target_type,
                            category=category,
                            country=country,
                            period=period,
                            criteria=cur_crit,
                            ranking_date=calc_ranking_date
                        )
                        
                        already_collected = 0
                        existing_df = pd.DataFrame()
                        if existing_filepath:
                            try:
                                existing_df = load_and_standardize_csv(existing_filepath, cur_crit)
                                already_collected = len(existing_df)
                                logger.info(f"이어서 수집: 기존 파일 발견 -> {existing_filepath} (기존 {already_collected}개)")
                            except Exception as csv_err:
                                logger.warning(f"기존 CSV 파일 읽기 실패 (새로 수집 진행): {csv_err}")
                                
                        if already_collected >= target_count:
                            logger.info(f"이미 {already_collected}개의 항목이 수집되어 목표치 {target_count}에 도달했습니다. 수집을 건너뜁니다.")
                            df = existing_df.head(target_count)
                            current_step += 1
                            progress_bar.progress(current_step / total_steps)
                        else:
                            df_new = crawler.crawl(
                                url=url,
                                target_type=target_type,
                                login_mode=login_mode,
                                target_count=target_count,
                                country=country,
                                period=period,
                                ranking_date=ranking_date,
                                ranking_criteria=cur_crit,
                                start_rank=already_collected,
                                keep_open=True,
                                category=category,
                                use_specific_date=use_specific_date
                            )
                            
                            current_step += 1
                            progress_bar.progress(current_step / total_steps)
                            
                            if len(existing_df) > 0 and len(df_new) > 0:
                                existing_df = standardize_dataframe_types(existing_df, cur_crit)
                                df_new = standardize_dataframe_types(df_new, cur_crit)
                                df = pd.concat([existing_df, df_new], ignore_index=True)
                                if 'Video ID' in df.columns:
                                    df = df.drop_duplicates(subset=['Video ID'], keep='last')
                                else:
                                    df = df.drop_duplicates(subset=['Video Title', 'Channel Name'], keep='last')
                            else:
                                df = df_new if len(df_new) > 0 else existing_df
                                
                            # Rank 값 1부터 정렬해서 재정의
                            if len(df) > 0 and 'Rank' in df.columns:
                                df = standardize_dataframe_types(df, cur_crit)
                                df = df.sort_values(by='Rank').reset_index(drop=True)
                                df['Rank'] = range(1, len(df) + 1)
                                
                            final_ranking_date = calc_ranking_date
                            if len(df) > 0 and 'Ranking Date' in df.columns:
                                first_val = df['Ranking Date'].iloc[0]
                                if pd.notna(first_val) and str(first_val) != 'N/A':
                                    final_ranking_date = str(first_val).strip()
                                    logger.info(f"[Save Path] 실제 감지된 날짜 기준으로 경로를 확정합니다: {final_ranking_date}")
    
                            filepath, filename = generate_safe_filepath(
                                base_dir=Config.OUTPUT_DIR,
                                target_type=target_type,
                                category=category,
                                country=country,
                                period=period,
                                criteria=cur_crit,
                                ranking_date=final_ranking_date,
                                extension='csv'
                            )
                            
                            if len(df) > 0:
                                metric_col = 'Views'
                                if cur_crit == '좋아요 순위':
                                    metric_col = 'Likes'
                                elif cur_crit == '댓글 순위':
                                    metric_col = 'Comments'
                                
                                csv_columns = ['Period', 'Ranking Date', 'Type', 'Country', 'Category', 'Criteria', 'Rank', 'Rank Change',
                                               'Video Title', metric_col, 'Upload Date', 'Tags',
                                               'Channel Name', 'Subscribers', 'Thumbnail', 'Video ID']
                                csv_df = df[[col for col in csv_columns if col in df.columns]]
                                csv_df.to_csv(filepath, index=False, encoding='utf-8-sig')
                                logger.info(f"✓ [CSV 저장 완료] 경로: {filepath} | 파일명: {filename}")
                                
                                total_updated_files_count += 1
                                total_updated_rows_count += len(df)
                                total_newly_crawled_rows += len(df_new) if 'df_new' in locals() and df_new is not None else len(df)
                            
                        if len(df) > 0:
                            db_count = db_handler.insert_dataframe(df, category, country, period, target_type)
                            db_handler.log_crawl_history(target_type, category, country, period, len(df), success=True)
                            
                            all_combined_data.extend(df.head(20).to_dict('records') if hasattr(df, 'to_dict') else [])
                            last_filepath = filepath
                            last_filename = filename
                            total_target_cats_count += 1
                            
                    else:
                        # 일괄 카테고리 수집
                        needs_crawl = []   # (cat, existing_filepath, existing_df, already_collected) 튜플 저장
                        skipped_data = []  # 이미 수집 완료된 데이터 목록
                        
                        for cat in all_categories:
                            batch_cat_name = f"batch_{cat}"
                            existing_filepath = find_existing_batch_file(
                                base_dir=Config.OUTPUT_DIR,
                                target_type=target_type,
                                category=batch_cat_name,
                                country=country,
                                period=period,
                                criteria=cur_crit,
                                ranking_date=calc_ranking_date
                            )
                            
                            already_collected = 0
                            existing_df = pd.DataFrame()
                            if existing_filepath:
                                try:
                                    existing_df = load_and_standardize_csv(existing_filepath, cur_crit)
                                    already_collected = len(existing_df)
                                except Exception as csv_err:
                                    logger.warning(f"기존 CSV 파일 읽기 실패 (새로 수집 진행): {csv_err}")
                                    
                            if already_collected >= target_count:
                                logger.info(f"카테고리 '{cat}'은 이미 {already_collected}개 수집 완료되었습니다. ({cur_crit})")
                                df_cat = existing_df.head(target_count)
                                if len(df_cat) > 0:
                                    skipped_data.extend(df_cat.to_dict('records'))
                                total_skip_cats_count += 1
                                current_step += 1
                                progress_bar.progress(current_step / total_steps)
                            else:
                                needs_crawl.append((cat, existing_filepath, existing_df, already_collected))
                        
                        all_data = list(skipped_data)
                        
                        if not needs_crawl:
                            logger.info(f"[{cur_crit}] 모든 카테고리가 이미 수집 목표치를 충족하였습니다. 크롤링 순회를 스킵합니다.")
                            status_text.success(f"✓ [{cur_crit}] 모든 카테고리가 이미 목표 개수를 충족하여 수집을 건너뜁니다.")
                        else:
                            for idx, (cat, existing_filepath, existing_df, already_collected) in enumerate(needs_crawl):
                                if st.session_state['stop_requested']:
                                    logger.info("🛑 사용자 중단 감지: 카테고리 순환을 중단합니다.")
                                    break
                                    
                                status_text.info(f"[{cur_crit}] 카테고리 수집 중 ({idx+1}/{len(needs_crawl)}): '{cat}' 진행 중...")
                                
                                try:
                                    url = build_url(target_type, cat, country, period, timestamp)
                                    df_cat_new = crawler.crawl(
                                        url=url,
                                        target_type=target_type,
                                        login_mode=login_mode,
                                        target_count=target_count,
                                        country=country,
                                        period=period,
                                        ranking_date=ranking_date,
                                        ranking_criteria=cur_crit,
                                        start_rank=already_collected,
                                        keep_open=True,
                                        category=cat,
                                        use_specific_date=use_specific_date
                                    )
                                    # 사용자의 다음 카테고리 스킵 감지
                                    if getattr(crawler, 'skip_requested', False):
                                        crawler.skip_requested = False  # 플래그 초기화
                                        logger.warning(f"⏯️ 사용자 요청에 의해 카테고리 '{cat}' 수집이 스킵되었습니다. 다음 카테고리로 넘어갑니다.")
                                        current_step += 1
                                        progress_bar.progress(current_step / total_steps)
                                        continue
                                        
                                    if len(existing_df) > 0 and len(df_cat_new) > 0:
                                        existing_df = standardize_dataframe_types(existing_df, cur_crit)
                                        df_cat_new = standardize_dataframe_types(df_cat_new, cur_crit)
                                        df_cat = pd.concat([existing_df, df_cat_new], ignore_index=True)
                                        if 'Video ID' in df_cat.columns:
                                            df_cat = df_cat.drop_duplicates(subset=['Video ID'], keep='last')
                                        else:
                                            df_cat = df_cat.drop_duplicates(subset=['Video Title', 'Channel Name'], keep='last')
                                    else:
                                        df_cat = df_cat_new if len(df_cat_new) > 0 else existing_df
                                        
                                    # Rank 값 1부터 정렬해서 재정의 (이가 빠지지 않도록 연속적인 순번 부여)
                                    if len(df_cat) > 0 and 'Rank' in df_cat.columns:
                                        df_cat = standardize_dataframe_types(df_cat, cur_crit)
                                        df_cat = df_cat.sort_values(by='Rank').reset_index(drop=True)
                                        df_cat['Rank'] = range(1, len(df_cat) + 1)
                                        
                                    # 실제 수집 데이터의 랭킹 날짜로 최종 네이밍 확정 (수집일이 아닌 랭킹 날짜 기준)
                                    final_ranking_date = calc_ranking_date
                                    if len(df_cat) > 0 and 'Ranking Date' in df_cat.columns:
                                        first_val = df_cat['Ranking Date'].iloc[0]
                                        if pd.notna(first_val) and str(first_val) != 'N/A':
                                            final_ranking_date = str(first_val).strip()
    
                                    batch_cat_name = f"batch_{cat}"
                                    filepath, filename = generate_safe_filepath(
                                        base_dir=Config.OUTPUT_DIR,
                                        target_type=target_type,
                                        category=batch_cat_name,
                                        country=country,
                                        period=period,
                                        criteria=cur_crit,
                                        ranking_date=final_ranking_date,
                                        extension='csv'
                                    )
                                        
                                    if len(df_cat) > 0:
                                        metric_col = 'Views'
                                        if cur_crit == '좋아요 순위':
                                            metric_col = 'Likes'
                                        elif cur_crit == '댓글 순위':
                                            metric_col = 'Comments'
                                        
                                        csv_columns = ['Period', 'Ranking Date', 'Type', 'Country', 'Category', 'Criteria', 'Rank', 'Rank Change',
                                                       'Video Title', metric_col, 'Upload Date', 'Tags',
                                                       'Channel Name', 'Subscribers', 'Thumbnail', 'Video ID']
                                        csv_df = df_cat[[col for col in csv_columns if col in df_cat.columns]]
                                        csv_df.to_csv(filepath, index=False, encoding='utf-8-sig')
                                        logger.info(f"✓ [CSV 저장 완료] 경로: {filepath} | 파일명: {os.path.basename(filepath)}")
                                        
                                        # 통계 정보 누적
                                        total_updated_files_count += 1
                                        total_updated_rows_count += len(df_cat)
                                        total_newly_crawled_rows += len(df_cat_new) if 'df_cat_new' in locals() and df_cat_new is not None else len(df_cat)
                                        
                                    if len(df_cat) > 0:
                                        all_data.extend(df_cat.to_dict('records'))
                                        db_handler.insert_dataframe(df_cat, cat, country, period, target_type)
                                        db_handler.log_crawl_history(target_type, cat, country, period, len(df_cat), success=True)
                                    else:
                                        total_failed_cats_list.append(f"{cat}({cur_crit})")
                                except Exception as cat_err:
                                    total_failed_cats_list.append(f"{cat}({cur_crit})")
                                    import traceback
                                    err_detail = traceback.format_exc()
                                    logger.error(f"Error in batch category '{cat}' ({cur_crit}): {cat_err}\n{err_detail}")
                                    db_handler.log_crawl_history(target_type, cat, country, period, 0, success=False, error_message=str(cat_err))
                                    
                                    # 윈도우 OS 알림 및 효과음 발송
                                    try:
                                        from modules.utils import show_notification, play_notification_sound
                                        play_notification_sound()
                                        show_notification(
                                            "유튜브 일괄 크롤러 기동 에러 발생",
                                            f"카테고리 '{cat}' 수집 중 에러가 발생하여 수집이 중단되었습니다: {cat_err}"
                                        )
                                    except Exception as notify_err:
                                        logger.debug(f"Failed to send exception notification: {notify_err}")
                                        
                                    # 에러 상황 세션 바인딩 및 루프 즉시 중단(중지)
                                    err_stats = {
                                        "target_cats_count": len(all_categories) * len(active_criteria_list),
                                        "target_limit": target_count,
                                        "skip_cats_count": total_skip_cats_count,
                                        "updated_files_count": total_updated_files_count,
                                        "updated_rows_count": total_updated_rows_count,
                                        "newly_crawled_rows": total_newly_crawled_rows,
                                        "failed_cats_count": len(total_failed_cats_list),
                                        "failed_cats_list": total_failed_cats_list
                                    }
                                    
                                    st.session_state['crawl_result'] = {
                                        "status": "error",
                                        "msg": f"✗ 일괄 크롤링 수집 실패: 카테고리 '{cat}' 수집 중 에러 발생 ({cat_err})",
                                        "stats": err_stats
                                    }
                                    break
                                finally:
                                    current_step += 1
                                    progress_bar.progress(current_step / total_steps)
                            
                        if all_data:
                            combined_df = pd.DataFrame(all_data)
                            
                            # 각 서브 카테고리별 수집 완성도 검증 및 summary_df / under_target_df 작성
                            summary_records = []
                            under_target_records = []
                            for cat in all_categories:
                                cat_df = combined_df[combined_df['Category'] == cat] if 'Category' in combined_df.columns else pd.DataFrame()
                                collected_count = len(cat_df)
                                status = "✓ 충족" if collected_count >= target_count else "⚠️ 미달"
                                shortage = max(0, target_count - collected_count)
                                
                                summary_records.append({
                                    "카테고리": cat,
                                    "목표 수량": target_count,
                                    "실제 수집 수량": collected_count,
                                    "부족분": shortage,
                                    "상태": status
                                })
                                
                                if collected_count < target_count:
                                    under_target_records.append({
                                        "카테고리": cat,
                                        "목표 수량": target_count,
                                        "실제 수집 수량": collected_count,
                                        "부족 수량": shortage
                                    })
                            
                            summary_df = pd.DataFrame(summary_records)
                            under_target_df = pd.DataFrame(under_target_records)
                            
                            final_comb_date = calc_ranking_date
                            if len(combined_df) > 0 and 'Ranking Date' in combined_df.columns:
                                first_val = combined_df['Ranking Date'].iloc[0]
                                if pd.notna(first_val) and str(first_val) != 'N/A':
                                    final_comb_date = str(first_val).strip()
    
                            filepath_comb, filename_comb = generate_safe_filepath(
                                base_dir=Config.OUTPUT_DIR,
                                target_type=target_type,
                                category='ALL',
                                country=country,
                                period=period,
                                criteria=cur_crit,
                                ranking_date=final_comb_date,
                                extension='csv'
                            )
                            
                            metric_col = 'Views'
                            if cur_crit == '좋아요 순위':
                                metric_col = 'Likes'
                            elif cur_crit == '댓글 순위':
                                metric_col = 'Comments'
                            
                            csv_columns = ['Period', 'Ranking Date', 'Type', 'Country', 'Category', 'Criteria', 'Rank', 'Rank Change',
                                           'Video Title', metric_col, 'Upload Date', 'Tags',
                                           'Channel Name', 'Subscribers', 'Thumbnail', 'Video ID']
                            csv_df = combined_df[[col for col in csv_columns if col in combined_df.columns]]
                            csv_df.to_csv(filepath_comb, index=False, encoding='utf-8-sig')
                            logger.info(f"✓ [통합 CSV 저장 완료] 경로: {filepath_comb} | 파일명: {filename_comb}")
                            
                            all_combined_data.extend(combined_df.head(20).to_dict('records') if hasattr(combined_df, 'to_dict') else [])
                            last_filepath = filepath_comb
                            last_filename = filename_comb
                            total_target_cats_count += len(all_categories)
                
                # 모든 수집 순회 성공 마무리
                progress_bar.progress(1.0)
                
                stats = {
                    "target_cats_count": len(all_categories) * len(active_criteria_list) if batch_mode else len(active_criteria_list),
                    "target_limit": target_count,
                    "skip_cats_count": total_skip_cats_count,
                    "updated_files_count": total_updated_files_count,
                    "updated_rows_count": total_updated_rows_count,
                    "newly_crawled_rows": total_newly_crawled_rows,
                    "failed_cats_count": len(total_failed_cats_list),
                    "failed_cats_list": total_failed_cats_list
                }
                
                logger.info("============================================================\n"
                            "🏆 [크롤링 최종 누적 수집 통계 요약]\n"
                            f"  - 총 목표 작업 수   : {stats['target_cats_count']}개\n"
                            f"  - 카테고리당 목표량  : {target_count}개\n"
                            f"  - 건너뛴 작업 수     : {total_skip_cats_count}개\n"
                            f"  - 실제 업데이트 파일 : {total_updated_files_count}개\n"
                            f"  - 업데이트 행 합계   : {total_updated_rows_count}개\n"
                            f"  - 신규 파싱 행 합계   : {total_newly_crawled_rows}개\n"
                            f"  - 실패 작업 수       : {len(total_failed_cats_list)}개\n"
                            "============================================================")
                
                if total_failed_cats_list:
                    st.session_state['crawl_result'] = {
                        "status": "error",
                        "is_batch": batch_mode,
                        "data": all_combined_data[:20],
                        "filepath": last_filepath,
                        "filename": last_filename,
                        "msg": f"⚠ 일부 작업 실패: 성공 {stats['updated_files_count']}/{stats['target_cats_count']}개 완료 (에러 발생 작업: {', '.join(total_failed_cats_list)})",
                        "stats": stats
                    }
                else:
                    if all_combined_data:
                        st.session_state['crawl_result'] = {
                            "status": "success",
                            "is_batch": batch_mode,
                            "summary_data": summary_df.to_dict('records') if 'summary_df' in locals() and hasattr(summary_df, 'to_dict') else [],
                            "under_target_data": under_target_df.to_dict('records') if 'under_target_df' in locals() and hasattr(under_target_df, 'to_dict') else [],
                            "data": all_combined_data[:20],
                            "filepath": last_filepath,
                            "filename": last_filename,
                            "target_count": target_count,
                            "msg": f"✓ 모든 수집 완료: 총 {total_updated_files_count}개 파일 업데이트 및 DB 저장 완료 ({stats['target_cats_count']}개 완료)",
                            "stats": stats
                        }
                    else:
                        st.session_state['crawl_result'] = {
                            "status": "warning",
                            "is_batch": batch_mode,
                            "msg": "⚠ 수집된 데이터가 없습니다."
                        }
                        
            except Exception as e:
                progress_bar.progress(1.0)
                
                l_failed = total_failed_cats_list if 'total_failed_cats_list' in locals() else []
                if 'cur_crit' in locals():
                    l_failed.append(f"전체({cur_crit})")
                else:
                    l_failed.append("시스템 예외")
                    
                err_stats = {
                    "target_cats_count": len(all_categories) * len(active_criteria_list) if 'active_criteria_list' in locals() and batch_mode else 1,
                    "target_limit": target_count if 'target_count' in locals() else 100,
                    "skip_cats_count": total_skip_cats_count if 'total_skip_cats_count' in locals() else 0,
                    "updated_files_count": total_updated_files_count if 'total_updated_files_count' in locals() else 0,
                    "updated_rows_count": total_updated_rows_count if 'total_updated_rows_count' in locals() else 0,
                    "newly_crawled_rows": total_newly_crawled_rows if 'total_newly_crawled_rows' in locals() else 0,
                    "failed_cats_count": len(l_failed),
                    "failed_cats_list": l_failed
                }
                
                logger.info("============================================================\n"
                            "🏆 [크롤링 최종 누적 수집 통계 요약 (실패)]\n"
                            f"  - 총 목표 작업 수   : {err_stats['target_cats_count']}개\n"
                            f"  - 실제 업데이트 파일 : {err_stats['updated_files_count']}개\n"
                            f"  - 업데이트 행 합계   : {err_stats['updated_rows_count']}개\n"
                            f"  - 실패 작업 수       : {len(l_failed)}개\n"
                            "============================================================")
                                            
                st.session_state['crawl_result'] = {
                    "status": "error",
                    "msg": f"✗ 크롤링 도중 예외가 발생했습니다: {e}",
                    "stats": err_stats
                }
                
                import traceback
                err_detail = traceback.format_exc()
                logger.error(f"Crawler error: {e}\n{err_detail}")
                
                try:
                    from modules.utils import show_notification, play_notification_sound
                    play_notification_sound()
                    show_notification(
                        "유튜브 크롤러 기동 에러 발생",
                        f"크롤링 동작 중 에러가 발생하여 중지되었습니다: {e}"
                    )
                except Exception as notify_err:
                    logger.debug(f"Failed to send exception notification: {notify_err}")
            finally:
                if 'crawler_instance' in st.session_state and st.session_state['crawler_instance'] is not None:
                    try:
                        st.session_state['crawler_instance'].close()
                    except:
                        pass
                logger.removeHandler(streamlit_handler)
                st.rerun()"""

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 906번째 줄(0-indexed 905)부터 1463번째 줄(0-indexed 1462)까지를 교체
lines = content.splitlines()

# lines[905:1463] 부분을 치환
pre_lines = lines[:905]
post_lines = lines[1463:]

# new_code는 이미 개행문자를 포함하고 있음
patched_content = '\\n'.join(pre_lines) + '\\n' + NEW_CODE + '\\n' + '\\n'.join(post_lines) + '\\n'

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(patched_content)

print("✓ app.py 패치 완료!")
